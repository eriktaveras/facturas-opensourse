import os
import base64
import io
import json
import requests
from datetime import datetime
from PIL import Image, ImageFile
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from models import Invoice, Setting, SessionLocal, Organization
from openai_service import OpenAIInvoiceProcessor
from redis_client import cache_get, cache_set, rate_limit, is_duplicate_message, invalidate_cache_pattern

# Permitir cargar imágenes truncadas
ImageFile.LOAD_TRUNCATED_IMAGES = True

class WhatsAppService:
    def __init__(self):
        self._refresh_config()
        self.openai_processor = OpenAIInvoiceProcessor()
        
    def _refresh_config(self):
        """Carga la configuración desde la base de datos o variables de entorno con caché Redis"""
        # Intentar cargar desde caché Redis primero
        cache_key = "settings:whatsapp:default"
        cached_config = cache_get(cache_key)
        if cached_config:
            self.evolution_url = cached_config.get("evolution_url", "")
            self.api_key = cached_config.get("evolution_apikey", "")
            self.instance_name = cached_config.get("evolution_instance", "")
            self.authorized_number = cached_config.get("authorized_whatsapp_number", "")
            print(f"⚡ WhatsApp Config desde caché: URL={self.evolution_url}, Instance={self.instance_name}, Auth={self.authorized_number}")
            return

        # Si no está en caché, cargar desde DB
        db = SessionLocal()
        try:
            org = db.query(Organization).first()
            org_id = org.id if org else None

            def get_val(key, default):
                query = db.query(Setting).filter(Setting.key == key)
                if org_id:
                    query = query.filter(Setting.organization_id == org_id)
                s = query.first()
                return s.value if s and s.value else default

            self.evolution_url = get_val("evolution_url", os.getenv("EVOLUTION_API_URL", ""))
            self.api_key = get_val("evolution_apikey", os.getenv("EVOLUTION_API_KEY", ""))
            self.instance_name = get_val("evolution_instance", os.getenv("EVOLUTION_INSTANCE_NAME", ""))
            self.authorized_number = get_val("authorized_whatsapp_number", os.getenv("AUTHORIZED_WHATSAPP_NUMBER", ""))

            # Guardar en caché por 1 hora
            config_data = {
                "evolution_url": self.evolution_url,
                "evolution_apikey": self.api_key,
                "evolution_instance": self.instance_name,
                "authorized_whatsapp_number": self.authorized_number
            }
            cache_set(cache_key, config_data, ttl=3600)

            print(f"⚙️ WhatsApp Config Cargada desde DB: URL={self.evolution_url}, Instance={self.instance_name}, Auth={self.authorized_number}")
        except Exception as e:
            print(f"⚠️ Error cargando config de WhatsApp: {e}")
            # Fallback a env vars si falla DB
            self.evolution_url = os.getenv("EVOLUTION_API_URL", "")
            self.api_key = os.getenv("EVOLUTION_API_KEY", "")
            self.instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "")
            self.authorized_number = os.getenv("AUTHORIZED_WHATSAPP_NUMBER", "")
        finally:
            db.close()
        
    def get_headers(self) -> Dict[str, str]:
        """Headers para Evolution API"""
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json"
        }
    
    async def process_webhook(self, payload: Dict[str, Any], db: Session) -> Dict[str, Any]:
        """
        Procesa webhook de Evolution API de forma simplificada con deduplicación y rate limiting
        """
        try:
            # Recargar config por si cambió en settings
            self._refresh_config()

            print(f"📱 Webhook recibido (evento: {payload.get('event')}): {json.dumps(payload, indent=2)[:500]}...")

            # Extraer información del mensaje para validaciones
            message_id = None
            sender = None

            if payload.get("event") == "messages.upsert" and "data" in payload:
                message_id = payload.get("data", {}).get("key", {}).get("id")
                sender = payload.get("sender", "")
            elif "object" in payload and "entry" in payload:
                messages = payload.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
                if messages:
                    message_id = messages[0].get("id")
                    sender = messages[0].get("from", "")

            # Deduplicación: Verificar si ya procesamos este mensaje
            if message_id and is_duplicate_message(message_id):
                print(f"🔄 Mensaje duplicado detectado: {message_id}")
                return {"status": "duplicate", "message": "Mensaje ya procesado", "message_id": message_id}

            # Rate Limiting: Limitar mensajes por remitente (10 mensajes por minuto)
            if sender:
                clean_sender = sender.replace("@s.whatsapp.net", "").replace("@c.us", "").split(":")[0]
                if not rate_limit(f"webhook:{clean_sender}", limit=10, window=60):
                    print(f"🚫 Rate limit excedido para {clean_sender}")
                    return {"status": "rate_limited", "message": "Demasiados mensajes, intenta más tarde"}

            # Procesar formato nativo de Evolution API
            if payload.get("event") == "messages.upsert" and "data" in payload:
                return await self._process_native_format(payload, db)

            # Procesar formato estándar WhatsApp Business API
            elif "object" in payload and "entry" in payload:
                return await self._process_standard_format(payload, db)

            print(f"⚠️ Formato de webhook no reconocido o evento ignorado: {payload.get('event')}")
            return {"status": "ignored", "message": "Formato no reconocido"}

        except Exception as e:
            print(f"❌ Error crítico en webhook: {e}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "error": str(e)}
    
    async def _process_native_format(self, payload: Dict[str, Any], db: Session) -> Dict[str, Any]:
        """Procesa formato nativo de Evolution API"""
        data = payload["data"]
        sender_phone = payload.get("sender", "")
        sender_name = data.get("pushName", "Usuario WhatsApp")
        message_id = data.get("key", {}).get("id", "unknown")
        
        # 🔒 VALIDACIÓN DE SEGURIDAD: Solo permitir mensajes del número autorizado
        clean_sender = sender_phone.replace("@s.whatsapp.net", "").replace("@c.us", "").replace("+", "").split(":")[0]
        
        if clean_sender != self.authorized_number:
            print(f"🚫 Acceso denegado: {clean_sender} intentó enviar una factura (Autorizado: {self.authorized_number})")
            return {
                "status": "unauthorized", 
                "message": f"Número no autorizado: {clean_sender}"
            }
        
        # Procesar imagen
        message_obj = data.get("message", {})
        if "imageMessage" in message_obj:
            print(f"📸 Procesando Imagen Original (ID: {message_id}) de {sender_name}")
            
            result = await self._process_image_message(
                message_id=message_id,
                sender_phone=sender_phone,
                sender_name=sender_name,
                db=db
            )
            
            await self._send_auto_response(sender_phone, result)
            return {"status": "success", "result": result}
        
        print(f"ℹ️ Mensaje de {clean_sender} no contenía imagen (imageMessage).")
        return {"status": "ignored", "message": "No es una imagen"}
        
        # Procesar comandos de texto
        if "message" in data and "conversation" in data["message"]:
            text = data["message"]["conversation"].lower().strip()
            if text in ["estado", "status", "help", "ayuda"]:
                await self._send_help_message(sender_phone)
                return {"status": "help_sent"}
        
        return {"status": "ignored", "message": "Mensaje no procesable"}
    
    async def _process_standard_format(self, payload: Dict[str, Any], db: Session) -> Dict[str, Any]:
        """Procesa formato estándar WhatsApp Business API"""
        try:
            # Extraer información del remitente del formato estándar
            entry = payload.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])
            
            if not messages:
                return {"status": "no_messages"}
            
            message = messages[0]
            sender_phone = message.get("from", "")
            
            # Obtener nombre del contacto
            sender_name = "Usuario WhatsApp"
            for contact in contacts:
                if contact.get("wa_id") == sender_phone:
                    sender_name = contact.get("profile", {}).get("name", "Usuario WhatsApp")
                    break
            
            # 🔒 VALIDACIÓN DE SEGURIDAD: Solo permitir mensajes del número autorizado
            # Limpiar el número del remitente para comparación
            clean_sender = sender_phone.replace("@s.whatsapp.net", "").replace("@c.us", "").replace("+", "")
            
            if clean_sender != self.authorized_number:
                print(f"🚫 Mensaje rechazado (formato estándar) - Número no autorizado: {clean_sender} (autorizado: {self.authorized_number})")
                return {
                    "status": "unauthorized", 
                    "message": f"Número no autorizado: {clean_sender}",
                    "authorized_number": self.authorized_number
                }
            
            print(f"✅ Número autorizado procesando mensaje estándar: {clean_sender}")
            
            # Procesar imagen si existe
            if message.get("type") == "image" and message.get("image"):
                print(f"📸 Imagen detectada de {sender_name} ({sender_phone}) - formato estándar")
                
                result = await self._process_image_message(
                    message_id=message.get("id", "unknown"),
                    sender_phone=sender_phone,
                    sender_name=sender_name,
                    db=db
                )
                
                # Enviar respuesta automática
                await self._send_auto_response(sender_phone, result)
                
                return {
                    "status": "success",
                    "processed_messages": 1,
                    "result": result
                }
            
            # Procesar comandos de texto
            elif message.get("type") == "text" and message.get("text"):
                text_content = message["text"].get("body", "").lower().strip()
                if text_content in ["estado", "status", "help", "ayuda"]:
                    await self._send_help_message(sender_phone)
                    return {"status": "help_sent"}
            
            return {"status": "standard_format_processed"}
            
        except Exception as e:
            print(f"❌ Error procesando formato estándar: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _process_image_message(
        self, 
        message_id: str, 
        sender_phone: str, 
        sender_name: str, 
        db: Session
    ) -> Dict[str, Any]:
        """
        Procesa mensaje de imagen de forma robusta
        """
        try:
            print(f"🔄 Procesando imagen: {message_id}")
            
            # Obtener imagen original desde Evolution API
            image_base64 = await self._get_image_from_evolution(message_id)
            
            if not image_base64:
                return {
                    "status": "error",
                    "error": "No se pudo obtener imagen desde Evolution API"
                }
            
            # Procesar y guardar imagen
            processed_image = await self._process_image_data(
                image_base64=image_base64,
                sender_phone=sender_phone,
                sender_name=sender_name,
                message_id=message_id
            )
            
            if not processed_image["success"]:
                return {
                    "status": "error",
                    "error": processed_image["error"]
                }
            
            # Crear registro en base de datos
            org = db.query(Organization).first()
            org_id = org.id if org else None
            invoice = Invoice(
                filename=processed_image["filename"],
                file_path=processed_image["file_path"],
                file_type="image",
                processed=False,
                description=f"WhatsApp de {sender_name} ({sender_phone})",
                organization_id=org_id
            )
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            
            print(f"💾 Imagen guardada: {invoice.id}")

            # Notificar que inicia el procesamiento con IA
            from websocket_service import websocket_manager
            await websocket_manager.broadcast({
                "type": "processing_started",
                "message": f"Analizando factura con IA...",
                "data": {
                    "invoice_id": invoice.id,
                    "sender": {
                        "name": sender_name,
                        "phone": sender_phone
                    }
                }
            }, org_id=org_id)

            # Procesar con OpenAI
            openai_result = await self._process_with_openai(invoice, db)
            
            return {
                "status": "success",
                "invoice_id": invoice.id,
                "openai_result": openai_result,
                "sender_info": {
                    "phone": sender_phone,
                    "name": sender_name,
                    "message_id": message_id
                }
            }
            
        except Exception as e:
            print(f"❌ Error procesando imagen: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _get_image_from_evolution(self, message_id: str) -> Optional[str]:
        """
        Obtiene imagen original desde Evolution API
        """
        try:
            payload = {
                "message": {
                    "key": {"id": message_id}
                },
                "convertToMp4": False
            }
            
            endpoint = f"/chat/getBase64FromMediaMessage/{self.instance_name}"
            url = f"{self.evolution_url}{endpoint}"
            
            print(f"📡 Solicitando Base64 a Evolution API: {url} (ID: {message_id})")
            
            response = requests.post(
                url,
                json=payload,
                headers=self.get_headers(),
                timeout=30
            )
            
            print(f"📊 Response Status: {response.status_code}")
            
            if response.status_code in [200, 201]:
                result = response.json()
                
                # Buscar base64 en la respuesta
                base64_data = self._extract_base64_from_response(result)
                
                if base64_data:
                    # Validar base64
                    try:
                        decoded = base64.b64decode(base64_data[:100] + "==") # Test decode header
                        print(f"✅ Base64 extraído exitosamente. Longitud: {len(base64_data)} caracteres.")
                        return base64_data
                    except Exception as e:
                        print(f"❌ Error al validar Base64: {e}")
                else:
                    print(f"❌ No se encontró campo Base64 en la respuesta de Evolution. Campos: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
            else:
                print(f"❌ Error en Evolution API (HTTP {response.status_code}): {response.text[:200]}")
                
            return None
            
        except Exception as e:
            print(f"❌ Excepción en _get_image_from_evolution: {e}")
            return None
    
    def _extract_base64_from_response(self, result: Any) -> Optional[str]:
        """Extrae base64 de la respuesta de Evolution API con logs de ayuda"""
        if isinstance(result, str):
            if len(result) > 100:
                print("🔍 Se detectó un string largo, asumiendo que es el Base64 directo.")
                return result
            return None
        
        if isinstance(result, dict):
            # Buscar en campos comunes
            for field in ['base64', 'mediaBase64', 'media', 'data', 'content']:
                if field in result and result[field]:
                    print(f"🔍 Base64 encontrado en campo: {field}")
                    return result[field]
            
            # Buscar anidado en message
            if 'message' in result:
                print("🔍 Buscando base64 dentro del objeto 'message'...")
                return self._extract_base64_from_response(result['message'])
            
            # Si es Evolution v2, a veces viene en result.base64 o similar
            if 'instance' in result:
                print("🔍 Estructura tipo instancia detectada, buscando base64...")
        
        return None
    
    async def _process_image_data(
        self, 
        image_base64: str, 
        sender_phone: str, 
        sender_name: str, 
        message_id: str
    ) -> Dict[str, Any]:
        """
        Procesa datos de imagen base64
        """
        try:
            # Decodificar base64
            image_data = base64.b64decode(image_base64)
            
            if len(image_data) < 100:
                return {"success": False, "error": f"Imagen muy pequeña: {len(image_data)} bytes"}
            
            # Procesar imagen con PIL
            processed_data = self._optimize_image_for_ocr(image_data)
            
            if not processed_data:
                return {"success": False, "error": "No se pudo procesar la imagen"}
            
            # Generar nombre de archivo
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            phone_clean = sender_phone.replace("@s.whatsapp.net", "").replace("@c.us", "")
            filename = f"whatsapp_{phone_clean}_{timestamp}.jpg"
            file_path = os.path.join("uploads", filename)
            
            # Crear directorio y guardar
            os.makedirs("uploads", exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(processed_data)
            
            return {
                "success": True,
                "filename": filename,
                "file_path": file_path,
                "size": len(processed_data)
            }
            
        except Exception as e:
            print(f"❌ Error procesando imagen: {e}")
            return {"success": False, "error": str(e)}
    
    def _optimize_image_for_ocr(self, image_data: bytes) -> Optional[bytes]:
        """
        Optimiza imagen para OCR
        """
        try:
            image_buffer = io.BytesIO(image_data)
            
            with Image.open(image_buffer) as img:
                print(f"🔍 Imagen original: {img.format} {img.width}x{img.height}")
                
                # Convertir a RGB
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Escalar si es muy pequeña (para mejor OCR)
                if img.width < 400 or img.height < 400:
                    scale_factor = max(400 / img.width, 400 / img.height)
                    new_size = (int(img.width * scale_factor), int(img.height * scale_factor))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                    print(f"📐 Escalada a {img.width}x{img.height}")
                
                # Redimensionar si es muy grande
                if img.width > 2048 or img.height > 2048:
                    img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                    print(f"📐 Reducida a {img.width}x{img.height}")
                
                # Guardar como JPEG optimizado
                output_buffer = io.BytesIO()
                img.save(output_buffer, format='JPEG', quality=95, optimize=True)
                
                result = output_buffer.getvalue()
                print(f"✅ Imagen optimizada: {len(result)} bytes")
                return result
                
        except Exception as e:
            print(f"❌ Error optimizando imagen: {e}")
            return None
    
    async def _process_with_openai(self, invoice: Invoice, db: Session) -> Dict[str, Any]:
        """
        Procesa factura con OpenAI
        """
        try:
            from fastapi.concurrency import run_in_threadpool
            
            # Ejecutar procesamiento síncrono en thread pool para no bloquear
            extracted_data = await run_in_threadpool(
                self.openai_processor.process_invoice,
                invoice.file_path, 
                "image", 
                invoice, 
                db
            )
            
            if extracted_data and "error" not in extracted_data:
                # Actualizar factura con datos extraídos
                invoice.vendor_name = extracted_data.get('vendor_name')
                invoice.invoice_number = extracted_data.get('invoice_number')
                
                if extracted_data.get('invoice_date'):
                    try:
                        invoice.invoice_date = datetime.strptime(extracted_data['invoice_date'], '%Y-%m-%d')
                    except:
                        pass
                
                invoice.total_amount = extracted_data.get('total_amount')
                invoice.tax_amount = extracted_data.get('tax_amount')
                invoice.currency = extracted_data.get('currency', 'USD')
                invoice.transaction_type = extracted_data.get('transaction_type')
                invoice.category = extracted_data.get('category')
                invoice.description = extracted_data.get('description')
                invoice.confidence_score = extracted_data.get('confidence')
                invoice.raw_extracted_data = json.dumps(extracted_data)
                invoice.processed = True
                
                db.commit()
                db.refresh(invoice)
                
                # Invalidar caché de estadísticas para reflejar cambios en tiempo real
                try:
                    invalidate_cache_pattern("stats:*")
                    print("🔄 Caché de estadísticas invalidado tras procesamiento WhatsApp")
                except Exception as e:
                    print(f"⚠️ Error invalidando caché: {e}")

                print(f"✅ OpenAI procesado exitosamente")
                return {"success": True, "data": extracted_data}
            else:
                error_msg = extracted_data.get('error', 'Error desconocido') if extracted_data else 'Sin datos'
                print(f"⚠️ Error OpenAI: {error_msg}")
                return {"success": False, "error": error_msg}
                
        except Exception as e:
            print(f"❌ Error OpenAI: {e}")
            return {"success": False, "error": str(e)}
    
    async def _send_auto_response(self, phone: str, result: Dict[str, Any]):
        """
        Envía respuesta automática
        """
        try:
            if result.get("status") == "success":
                invoice_id = result.get("invoice_id")
                message = f"✅ ¡Factura procesada exitosamente!\n\n📄 ID: {invoice_id}\n\nPuedes consultar los detalles en el sistema. ¡Gracias!"
            else:
                error = result.get("error", "Error desconocido")
                if "Evolution API" in error:
                    message = """⚠️ *No se pudo obtener la imagen*

El sistema no pudo acceder a tu imagen. Esto puede deberse a:

• Imagen muy antigua (ya no disponible en WhatsApp)
• Problema temporal de conectividad
• Formato de imagen no soportado

🔄 *Por favor intenta:*
1. Enviar la imagen nuevamente
2. Usar una imagen diferente
3. Enviar como documento si el problema persiste"""
                else:
                    message = "❌ Hubo un problema procesando tu factura. Por favor, intenta nuevamente."
            
            await self.send_message(phone, message)
            
        except Exception as e:
            print(f"❌ Error enviando respuesta: {e}")
    
    async def _send_help_message(self, phone: str):
        """Envía mensaje de ayuda"""
        help_text = """🤖 *Sistema de Facturas - WhatsApp Bot*

📸 *Envía una imagen de tu factura* y la procesaré automáticamente

💡 *Para mejores resultados:*
• Envía como *documento* (no como foto comprimida)
• Asegúrate de que el texto sea legible
• Usa buena iluminación y enfoque

📋 *Comandos disponibles:*
• `estado` - Ver estado del sistema
• `ayuda` - Ver este mensaje

✨ *Funciones:*
• Extracción automática de datos
• Clasificación de ingresos/egresos  
• Análisis con IA

¡Envía tu factura cuando quieras! 📄"""
        
        await self.send_message(phone, help_text)
    
    async def send_message(self, phone: str, message: str) -> Dict[str, Any]:
        """
        Envía mensaje por WhatsApp
        """
        try:
            send_data = {
                "number": phone,
                "text": message
            }
            
            response = requests.post(
                f"{self.evolution_url}/message/sendText/{self.instance_name}",
                json=send_data,
                headers=self.get_headers(),
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"✅ Mensaje enviado a {phone}")
                return {"status": "success", "response": response.json()}
            else:
                print(f"❌ Error enviando mensaje: {response.status_code}")
                return {"status": "error", "code": response.status_code}
                
        except Exception as e:
            print(f"❌ Error enviando mensaje: {e}")
            return {"status": "error", "error": str(e)}
    
    async def get_instance_status(self) -> Dict[str, Any]:
        """
        Verifica estado de la instancia
        """
        try:
            response = requests.get(
                f"{self.evolution_url}/instance/connectionState/{self.instance_name}",
                headers=self.get_headers(),
                timeout=10
            )
            
            if response.status_code == 200:
                return {"status": "success", "data": response.json()}
            else:
                return {"status": "error", "code": response.status_code}
                
        except Exception as e:
            return {"status": "error", "error": str(e)} 
