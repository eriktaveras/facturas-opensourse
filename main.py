from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_
from models import get_db, Invoice, Base, engine, init_database, Setting, UserSetting, Notification, User, WebhookEndpoint, Organization
from openai_service import OpenAIInvoiceProcessor
from websocket_service import websocket_manager, start_heartbeat_task
from whatsapp_service import WhatsAppService
from cost_control_service import CostControlService
from webhook_sender import WebhookSender
from export_service import ExportService
from auth import verify_password, create_access_token, get_password_hash, get_current_active_user, SECRET_KEY, ALGORITHM
from jose import jwt, JWTError
from redis_client import cache_get, cache_set, invalidate_cache_pattern, get_cache_stats
import os
import shutil
from datetime import datetime, timedelta
import json
from typing import List, Optional, Dict, Any, Union
import mimetypes
from PIL import Image
import io
import base64
import requests
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import logging
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import StreamingResponse

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

app = FastAPI(title="Sistema de Gestión de Facturas", version="1.0.0")

# Inicializar servicios
webhook_sender = WebhookSender()
export_service = ExportService()

# --- AUTH HELPER FOR COOKIES ---
async def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        return None
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
    except JWTError:
        return None
    
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        return None
        
    return user

async def get_current_user_from_websocket(websocket: WebSocket, db: Session):
    token = websocket.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
    except JWTError:
        return None
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        return None
    return user

# Exception Handlers
@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    return templates.TemplateResponse("500.html", {"request": request}, status_code=500)

@app.on_event("startup")
async def startup_event():
    """Inicializar la base de datos al arrancar la aplicación"""
    logger.info("🚀 Iniciando aplicación...")
    init_database()
    
    # Crear usuario admin por defecto si no existe
    db = next(get_db())
    try:
        admin_email = os.getenv("ADMIN_EMAIL")
        if not admin_email:
            raise RuntimeError("ADMIN_EMAIL no configurado. Debes definirlo en el entorno.")
        if not db.query(User).filter(User.email == admin_email).first():
            admin_password = os.getenv("ADMIN_PASSWORD")
            if not admin_password:
                raise RuntimeError("ADMIN_PASSWORD no configurada. Debes definirla en el entorno.")
            else:
                logger.info(f"👤 Creando usuario admin por defecto: {admin_email}")
                # Truncar contraseña a 72 bytes (límite de bcrypt)
                if len(admin_password.encode('utf-8')) > 72:
                    admin_password = admin_password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
                    logger.warning("⚠️ Contraseña admin truncada a 72 bytes (límite bcrypt)")
                hashed_pwd = get_password_hash(admin_password)
                default_org = get_default_org(db)
                user = User(
                    email=admin_email,
                    hashed_password=hashed_pwd,
                    full_name="Admin User",
                    is_superuser=True,
                    organization_id=default_org.id
                )
                db.add(user)
                db.commit()
    except Exception as e:
        logger.error(f"Error creando admin user: {e}")
    finally:
        db.close()
    
    # Iniciar tarea de heartbeat para WebSocket
    import asyncio
    asyncio.create_task(start_heartbeat_task())
    
    logger.info("✅ Aplicación iniciada correctamente")
    logger.info("📡 WebSocket habilitado para notificaciones en tiempo real")



# Crear directorios necesarios si no existen
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

# Configurar archivos estáticos y templates
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
templates = Jinja2Templates(directory="templates")

def get_default_org(db: Session) -> Organization:
    org = db.query(Organization).first()
    if not org:
        org = Organization(name="Mi Empresa S.A.", tax_id="", plan="Free Plan")
        db.add(org)
        db.commit()
        db.refresh(org)
    return org

def get_org_id(user: Optional[User], db: Session) -> int:
    if user and user.organization_id:
        return user.organization_id
    return get_default_org(db).id

def get_company_context(db: Session, user: Optional[User]) -> dict:
    org = db.query(Organization).filter(Organization.id == get_org_id(user, db)).first()
    if not org:
        org = get_default_org(db)
    return {
        "company_name": org.name or "Mi Empresa S.A.",
        "company_tax_id": org.tax_id or "",
        "company_plan": org.plan or "Free Plan"
    }

# Inicializar servicios
openai_processor = OpenAIInvoiceProcessor()
whatsapp_service = WhatsAppService()
cost_control = CostControlService()

# Tipos de archivo permitidos
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff'}
ALLOWED_PDF_EXTENSIONS = {'.pdf'}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_PDF_EXTENSIONS

# ===========================================
# AUTENTICACIÓN
# ===========================================

@app.post("/token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Usuario inactivo")
        
    access_token_expires = timedelta(minutes=300)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("access_token")
    return response

# ===========================================
# CHAT FINANCIERO (CFO AI)
# ===========================================

class ChatRequest(BaseModel):
    query: str

@app.post("/api/chat/finance")
async def chat_finance(request: ChatRequest, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """
    Endpoint para chatear con los datos financieros (CFO Virtual)
    """
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)

    try:
        # Obtener contexto: últimas 50 facturas procesadas
        # Optimizamos seleccionando solo campos relevantes para ahorrar tokens
        invoices = db.query(Invoice).filter(
            Invoice.processed == True,
            Invoice.organization_id == org_id
        ).order_by(desc(Invoice.invoice_date)).limit(50).all()

        # Construir contexto ligero
        context_data = []
        for inv in invoices:
            context_data.append({
                "fecha": inv.invoice_date.strftime("%Y-%m-%d") if inv.invoice_date else "N/A",
                "proveedor": inv.vendor_name,
                "total": inv.total_amount,
                "moneda": inv.currency,
                "tipo": inv.transaction_type, # expense/income
                "categoria": inv.category
            })
        
        # Si no hay facturas, dar contexto vacío pero válido
        if not context_data:
            return {"answer": "No veo ninguna factura registrada en el sistema aún. Sube algunas facturas para que pueda ayudarte con tus finanzas."}

        # Llamar al servicio de OpenAI
        answer = openai_processor.process_finance_chat(request.query, context_data, org_id=org_id, user_id=user.id)
        
        return {"answer": answer}

    except Exception as e:
        logger.error(f"Error en chat finance: {e}")
        raise HTTPException(status_code=500, detail="Error procesando tu consulta financiera.")

# ===========================================
# GESTIÓN DE NOTIFICACIONES
# ===========================================

@app.get("/api/notifications")
async def get_notifications(limit: int = 20, unread_only: bool = False, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Obtener notificaciones"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    query = db.query(Notification).filter(Notification.organization_id == org_id)
    
    if unread_only:
        query = query.filter(Notification.read == False)
        
    notifications = query.order_by(desc(Notification.created_at)).limit(limit).all()
    
    return [n.to_dict() for n in notifications]

@app.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Marcar notificación como leída"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    notification = db.query(Notification).filter(Notification.id == notification_id, Notification.organization_id == org_id).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    
    notification.read = True
    db.commit()
    return {"status": "success"}

@app.post("/api/notifications/read-all")
async def mark_all_notifications_read(user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Marcar todas las notificaciones como leídas"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    db.query(Notification).filter(Notification.read == False, Notification.organization_id == org_id).update({"read": True})
    db.commit()
    return {"status": "success"}

# ===========================================
# GESTIÓN DE CONFIGURACIÓN
# ===========================================

class SettingUpdate(BaseModel):
    key: str
    value: Union[str, int, float, bool]
    category: Optional[str] = "general"
    type: Optional[str] = "string"

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Página de configuración del sistema"""
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        **get_company_context(db, user)
    })

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Página de reportes financieros"""
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "user": user,
        **get_company_context(db, user)
    })

@app.get("/api/settings")
async def get_settings(user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Obtener todas las configuraciones"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    settings = db.query(Setting).all()
    user_settings = db.query(UserSetting).filter(UserSetting.user_id == user.id).all()
    user_settings_map = {s.key: s for s in user_settings}
    result = {}
    
    # Agrupar por categoría
    for setting in settings:
        if setting.category not in result:
            result[setting.category] = []
        
        # Convertir valor según el tipo
        resolved = user_settings_map.get(setting.key) or setting
        value = resolved.value
        if resolved.type == 'boolean':
            value = str(resolved.value).lower() == 'true'
        elif resolved.type == 'int':
            try:
                value = int(resolved.value)
            except:
                value = 0
        elif resolved.type == 'float':
            try:
                value = float(resolved.value)
            except:
                value = 0.0
                
        result[setting.category].append({
            "key": setting.key,
            "value": value,
            "type": resolved.type or setting.type,
            "description": resolved.description or setting.description,
            "category": resolved.category or setting.category,
            "source": "user" if setting.key in user_settings_map else "default"
        })

    # Incluir settings personalizados del usuario que no existen en defaults
    for setting in user_settings:
        if setting.key not in {s.key for s in settings}:
            if setting.category not in result:
                result[setting.category] = []
            value = setting.value
            if setting.type == 'boolean':
                value = str(setting.value).lower() == 'true'
            elif setting.type == 'int':
                try:
                    value = int(setting.value)
                except:
                    value = 0
            elif setting.type == 'float':
                try:
                    value = float(setting.value)
                except:
                    value = 0.0
            result[setting.category].append({
                "key": setting.key,
                "value": value,
                "type": setting.type,
                "description": setting.description or "Configuración personalizada",
                "category": setting.category,
                "source": "user"
            })
        
    return result

@app.post("/api/settings")
async def update_settings(updates: List[SettingUpdate], user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Actualizar múltiples configuraciones"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        org_id = get_org_id(user, db)
        updated_count = 0
        for update in updates:
            # Convertir a string para almacenamiento
            str_value = str(update.value)
            if isinstance(update.value, bool) or update.type == 'boolean':
                str_value = str(update.value).lower()
            
            default_setting = db.query(Setting).filter(Setting.key == update.key).first()
            setting = db.query(UserSetting).filter(
                UserSetting.key == update.key,
                UserSetting.user_id == user.id
            ).first()
            
            if setting:
                setting.value = str_value
                setting.type = update.type or setting.type
                setting.category = update.category or setting.category
                updated_count += 1
            else:
                # Crear nueva configuración si no existe
                new_setting = UserSetting(
                    key=update.key,
                    value=str_value,
                    category=update.category or (default_setting.category if default_setting else "general"),
                    type=update.type or (default_setting.type if default_setting else "string"),
                    description=(default_setting.description if default_setting else f"Configuración creada automáticamente: {update.key}"),
                    user_id=user.id
                )
                db.add(new_setting)
                updated_count += 1
        
        db.commit()

        # Invalidar caché de settings
        invalidate_cache_pattern("settings:*")
        logger.info("🗑️ Caché de settings invalidado")

        return {"status": "success", "updated": updated_count}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def get_file_type(filename: str) -> str:
    """Determina el tipo de archivo basado en la extensión"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        return "image"
    elif ext in ALLOWED_PDF_EXTENSIONS:
        return "pdf"
    else:
        raise ValueError(f"Tipo de archivo no permitido: {ext}")

def optimize_image(image_path: str, max_width: int = 800, quality: int = 85) -> str:
    """Optimiza una imagen para reducir su tamaño"""
    try:
        with Image.open(image_path) as img:
            # Convertir a RGB si es necesario
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            
            # Redimensionar si es muy grande
            if img.width > max_width:
                ratio = max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            
            # Guardar en memoria como JPEG optimizado
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            buffer.seek(0)
            
            # Convertir a base64 para mostrar en navegador
            img_data = base64.b64encode(buffer.getvalue()).decode()
            return f"data:image/jpeg;base64,{img_data}"
            
    except Exception as e:
        print(f"Error optimizando imagen: {e}")
        return None

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Página principal"""
    if not user:
        return templates.TemplateResponse("landing.html", {"request": request})
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        **get_company_context(db, user)
    })

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Endpoint WebSocket para notificaciones en tiempo real
    """
    db = next(get_db())
    try:
        user = await get_current_user_from_websocket(websocket, db)
        if not user:
            await websocket.close(code=1008)
            return
        org_id = get_org_id(user, db)
        await websocket_manager.connect(websocket, org_id)
    finally:
        db.close()
    try:
        while True:
            # Esperar mensajes del cliente (opcional para mantener conexión)
            data = await websocket.receive_text()
            # Procesar mensajes del cliente si es necesario
            if data == "ping":
                await websocket_manager.send_personal_message(
                    {"type": "pong", "message": "Conexión activa"}, 
                    websocket
                )
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

@app.get("/test-invoice/{invoice_id}")
async def test_invoice(invoice_id: int):
    """Test endpoint"""
    return {"test": "working", "invoice_id": invoice_id}

@app.get("/invoice/{invoice_id}")
async def invoice_detail_json(invoice_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """API JSON para detalle de factura"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return {"invoice": invoice.to_dict(), "status": "success"}

@app.get("/invoice/{invoice_id}/view", response_class=HTMLResponse)
async def invoice_detail_view(request: Request, invoice_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Página de detalle de factura"""
    try:
        if not user:
            return RedirectResponse(url="/login")
        org_id = get_org_id(user, db)
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Factura no encontrada")
        
        return templates.TemplateResponse("invoice_detail.html", {
            "request": request,
            "invoice": invoice,
            **get_company_context(db, user)
        })
    except Exception as e:
        print(f"Error in invoice_detail: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

@app.post("/upload")
async def upload_files(files: List[UploadFile] = File(...), user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Subir múltiples archivos de facturas"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    results = []
    org_id = get_org_id(user, db)
    
    for file in files:
        try:
            # Validar extensión de archivo
            file_ext = os.path.splitext(file.filename)[1].lower()
            if file_ext not in ALLOWED_EXTENSIONS:
                results.append({
                    "filename": file.filename,
                    "success": False,
                    "error": f"Tipo de archivo no permitido: {file_ext}"
                })
                continue
            
            # Generar nombre único para el archivo
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_filename = f"{timestamp}_{file.filename}"
            file_path = os.path.join("uploads", safe_filename)
            
            # Guardar archivo
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Determinar tipo de archivo
            file_type = get_file_type(file.filename)
            
            # Crear registro en base de datos
            invoice = Invoice(
                filename=file.filename,
                file_path=file_path,
                file_type=file_type,
                processed=False,
                organization_id=org_id
            )
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            
            results.append({
                "filename": file.filename,
                "success": True,
                "invoice_id": invoice.id,
                "message": "Archivo subido correctamente"
            })
            
            # Notificar via WebSocket
            await websocket_manager.notify_new_invoice_upload(invoice.id, file.filename, org_id)
            
        except Exception as e:
            results.append({
                "filename": file.filename,
                "success": False,
                "error": str(e)
            })
    
    return {"results": results}

@app.post("/process/{invoice_id}")
async def process_invoice(invoice_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Procesar una factura con OpenAI"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    # Bloquear la fila para evitar condiciones de carrera (doble clic)
    # En SQLite esto puede bloquear la tabla, en Postgres bloquea la fila
    org_id = get_org_id(user, db)
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).with_for_update().first()
    
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    
    if invoice.processed:
        return {"message": "Factura ya procesada", "invoice": invoice.to_dict()}
    
    try:
        # Procesar con OpenAI en un thread separado para no bloquear el Event Loop
        from fastapi.concurrency import run_in_threadpool
        
        extracted_data = await run_in_threadpool(
            openai_processor.process_invoice,
            invoice.file_path,
            invoice.file_type,
            invoice,
            db,
            user.id
        )
        
        if extracted_data and "error" not in extracted_data:
            # Actualizar invoice con datos extraídos
            invoice.vendor_name = extracted_data.get('vendor_name')
            invoice.invoice_number = extracted_data.get('invoice_number')
            
            # Convertir fecha string a datetime
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
            invoice.goods_services_type = extracted_data.get('goods_services_type')

            # Nuevos campos fiscales y de país
            invoice.vendor_country = extracted_data.get('vendor_country')
            invoice.vendor_tax_id = extracted_data.get('vendor_tax_id')
            invoice.vendor_fiscal_address = extracted_data.get('vendor_fiscal_address')
            invoice.country_detection_method = extracted_data.get('country_detection_method')
            invoice.country_confidence = extracted_data.get('country_confidence')

            # Líneas de productos (JSON)
            if extracted_data.get('line_items'):
                invoice.line_items_data = json.dumps(extracted_data['line_items'], ensure_ascii=False)
            else:
                invoice.line_items_data = "[]"

            # Detectar Duplicados
            if extracted_data.get('invoice_number') and extracted_data.get('vendor_name'):
                existing = db.query(Invoice).filter(
                    Invoice.invoice_number == extracted_data['invoice_number'],
                    Invoice.vendor_name == extracted_data['vendor_name'],
                    Invoice.id != invoice_id,
                    Invoice.processed == True,
                    Invoice.organization_id == org_id
                ).first()
                
                if existing:
                    warnings = extracted_data.get('audit_warnings', [])
                    if not isinstance(warnings, list): warnings = []
                    warnings.insert(0, f"DUPLICADO: Ya existe la factura #{existing.id}")
                    extracted_data['audit_warnings'] = warnings

            # Guardar alertas de auditoría
            if extracted_data.get('audit_warnings'):
                invoice.audit_flags = json.dumps(extracted_data['audit_warnings'], ensure_ascii=False)
            else:
                invoice.audit_flags = "[]"
                
            invoice.raw_extracted_data = json.dumps(extracted_data)
            invoice.processed = True
            
            db.commit()
            db.refresh(invoice)

            # Invalidar caché de estadísticas (ya que cambió data)
            invalidate_cache_pattern("stats:*")

            # Disparar Webhook: invoice.processed
            try:
                # Ejecutar en segundo plano para no demorar la respuesta
                from fastapi.concurrency import run_in_threadpool
                await run_in_threadpool(
                    webhook_sender.trigger_event,
                    db, 
                    "invoice.processed", 
                    invoice.to_dict(),
                    org_id
                )
            except Exception as e:
                print(f"⚠️ Error disparando webhook: {e}")
            
            return {
                "message": "Factura procesada exitosamente",
                "invoice": invoice.to_dict(),
                "extracted_data": extracted_data
            }
        else:
            error_msg = extracted_data.get('error', 'No se pudieron extraer datos') if extracted_data else 'Error desconocido'
            return {"message": "Error al procesar la factura", "error": error_msg}
    
    except Exception as e:
        return {"message": "Error al procesar la factura", "error": str(e)}

@app.get("/invoices")
async def get_invoices(
    skip: int = 0,
    limit: int = 100,
    transaction_type: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_from_cookie),
    db: Session = Depends(get_db)
):
    """Obtener lista de facturas con filtros opcionales"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    query = db.query(Invoice).filter(Invoice.organization_id == org_id)
    
    if transaction_type:
        query = query.filter(Invoice.transaction_type == transaction_type)
    
    if category:
        query = query.filter(Invoice.category == category)

    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(
            Invoice.vendor_name.ilike(pattern),
            Invoice.invoice_number.ilike(pattern),
            Invoice.description.ilike(pattern)
        ))
    
    invoices = query.order_by(desc(Invoice.created_at)).offset(skip).limit(limit).all()
    
    return {
        "invoices": [invoice.to_dict() for invoice in invoices],
        "total": query.count()
    }

@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Obtener detalles de una factura específica"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    
    return invoice.to_dict()

@app.get("/invoice/{invoice_id}/optimized-image")
async def get_optimized_image(invoice_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Obtener imagen optimizada de una factura"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    
    if invoice.file_type != "image":
        raise HTTPException(status_code=400, detail="La factura no es una imagen")
    
    if not os.path.exists(invoice.file_path):
        raise HTTPException(status_code=404, detail="Archivo de imagen no encontrado")
    
    optimized_data = optimize_image(invoice.file_path)
    if not optimized_data:
        raise HTTPException(status_code=500, detail="Error al optimizar imagen")
    
    return {"optimized_image": optimized_data}

@app.put("/invoices/{invoice_id}")
async def update_invoice(invoice_id: int, invoice_data: dict, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Actualizar datos de una factura"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    
    # Actualizar campos permitidos
    updateable_fields = [
        'vendor_name', 'invoice_number', 'total_amount', 'tax_amount',
        'currency', 'transaction_type', 'category', 'description',
        'vendor_country', 'vendor_tax_id', 'vendor_fiscal_address',
        'goods_services_type'
    ]
    
    for field in updateable_fields:
        if field in invoice_data:
            setattr(invoice, field, invoice_data[field])
    
    # Actualizar fecha si se proporciona
    if 'invoice_date' in invoice_data:
        try:
            invoice.invoice_date = datetime.strptime(invoice_data['invoice_date'], '%Y-%m-%d')
        except:
            pass
    
    invoice.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(invoice)
    
    return invoice.to_dict()

@app.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Eliminar una factura"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    
    # Eliminar archivo físico
    if os.path.exists(invoice.file_path):
        os.remove(invoice.file_path)
    
    db.delete(invoice)
    db.commit()
    
    return {"message": "Factura eliminada exitosamente"}

class BulkActionRequest(BaseModel):
    invoice_ids: List[int]

@app.post("/api/invoices/bulk-delete")
async def bulk_delete_invoices(action: BulkActionRequest, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Eliminar múltiples facturas"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    if not action.invoice_ids:
        return {"message": "No se seleccionaron facturas", "count": 0}
        
    invoices = db.query(Invoice).filter(
        Invoice.id.in_(action.invoice_ids),
        Invoice.organization_id == org_id
    ).all()
    count = 0
    
    for invoice in invoices:
        try:
            # Eliminar archivo físico
            if invoice.file_path and os.path.exists(invoice.file_path):
                os.remove(invoice.file_path)
            db.delete(invoice)
            count += 1
        except Exception as e:
            print(f"Error eliminando factura {invoice.id}: {e}")
            
    db.commit()
    return {"message": "Facturas eliminadas exitosamente", "count": count}

@app.post("/api/invoices/bulk-process")
async def bulk_process_invoices(action: BulkActionRequest, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Procesar múltiples facturas pendientes"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    if not action.invoice_ids:
        return {"message": "No se seleccionaron facturas", "count": 0}
        
    invoices = db.query(Invoice).filter(
        Invoice.id.in_(action.invoice_ids),
        Invoice.processed == False,
        Invoice.organization_id == org_id
    ).all()
    
    success_count = 0
    errors = []
    
    # Importante: Procesar en background o threading para no bloquear
    # Nota: Para volúmenes grandes, usar Celery/RQ. Aquí usamos threadpool simple.
    from fastapi.concurrency import run_in_threadpool
    
    for invoice in invoices:
        try:
            # Reutilizar lógica de process_invoice
            extracted_data = await run_in_threadpool(
                openai_processor.process_invoice,
                invoice.file_path,
                invoice.file_type,
                invoice,
                db,
                user.id
            )
            
            if extracted_data and "error" not in extracted_data:
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
                invoice.goods_services_type = extracted_data.get('goods_services_type')
                
                # Detectar Duplicados
                if extracted_data.get('invoice_number') and extracted_data.get('vendor_name'):
                    existing = db.query(Invoice).filter(
                        Invoice.invoice_number == extracted_data['invoice_number'],
                        Invoice.vendor_name == extracted_data['vendor_name'],
                        Invoice.id != invoice.id,
                        Invoice.processed == True,
                        Invoice.organization_id == org_id
                    ).first()
                    
                    if existing:
                        warnings = extracted_data.get('audit_warnings', [])
                        if not isinstance(warnings, list): warnings = []
                        warnings.insert(0, f"DUPLICADO: Ya existe la factura #{existing.id}")
                        extracted_data['audit_warnings'] = warnings
                
                # Guardar alertas de auditoría
                if extracted_data.get('audit_warnings'):
                    invoice.audit_flags = json.dumps(extracted_data['audit_warnings'], ensure_ascii=False)
                else:
                    invoice.audit_flags = "[]"
                    
                invoice.raw_extracted_data = json.dumps(extracted_data)
                invoice.processed = True
                success_count += 1
                
                # Disparar Webhook
                webhook_sender.trigger_event(db, "invoice.processed", invoice.to_dict(), org_id=org_id)
            else:
                errors.append(f"ID {invoice.id}: {extracted_data.get('error')}")
                
        except Exception as e:
            errors.append(f"ID {invoice.id}: {str(e)}")
            
    db.commit()
    
    return {
        "message": f"Procesamiento completado. {success_count} exitosos.",
        "success_count": success_count,
        "errors": errors
    }

class ExportRequest(BaseModel):
    invoice_ids: List[int]
    format: str = "csv" # csv, quickbooks, quickbooks_bills, xero, odoo, contaplus, json, dgii_606, excel

class WebhookPushRequest(BaseModel):
    invoice_ids: List[int]
    event: Optional[str] = "invoices.exported"

@app.post("/api/invoices/export")
async def export_invoices(action: ExportRequest, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Exportar facturas seleccionadas a diferentes formatos"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    if not action.invoice_ids:
        raise HTTPException(status_code=400, detail="No se seleccionaron facturas")
        
    invoices = db.query(Invoice).filter(
        Invoice.id.in_(action.invoice_ids),
        Invoice.organization_id == org_id
    ).all()
    
    if not invoices:
        raise HTTPException(status_code=404, detail="No se encontraron facturas")
        
    output = ""
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    filename = f"export_{action.format}_{timestamp}"
    media_type = "text/csv"
    
    try:
        if action.format == "quickbooks":
            output = export_service.export_quickbooks(invoices)
            filename += ".csv"
        elif action.format == "quickbooks_bills":
            output = export_service.export_quickbooks_bills(invoices)
            filename += ".csv"
        elif action.format == "xero":
            output = export_service.export_xero_bills(invoices)
            filename += ".csv"
        elif action.format == "odoo":
            output = export_service.export_odoo_vendor_bills(invoices)
            filename += ".csv"
        elif action.format == "contaplus":
            output = export_service.export_contaplus(invoices)
            filename += ".csv"
        elif action.format == "json":
            output = export_service.export_json(invoices)
            media_type = "application/json"
            filename += ".json"
        elif action.format == "dgii_606":
            org = db.query(Organization).filter(Organization.id == org_id).first()
            report_rnc = org.tax_id if org else None
            output = export_service.export_dgii_606(invoices, report_rnc=report_rnc)
            media_type = "application/vnd.ms-excel"
            filename += ".xls"
        elif action.format == "excel":
            output = export_service.export_excel_generic(invoices)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename += ".xlsx"
        else:
            # Default CSV
            output = export_service.export_csv_generic(invoices)
            filename += ".csv"
            
        # Retornar como archivo descargable
        if media_type in ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"]:
            return StreamingResponse(
                io.BytesIO(output),
                media_type=media_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        return StreamingResponse(
            io.StringIO(output),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Error exporting: {e}")
        raise HTTPException(status_code=500, detail=f"Error generando exportación: {str(e)}")

@app.post("/api/invoices/push-webhook")
async def push_invoices_webhook(payload: WebhookPushRequest, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Enviar JSON estructurado a webhooks configurados"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    if not payload.invoice_ids:
        raise HTTPException(status_code=400, detail="No se seleccionaron facturas")

    invoices = db.query(Invoice).filter(
        Invoice.id.in_(payload.invoice_ids),
        Invoice.organization_id == org_id
    ).all()
    if not invoices:
        raise HTTPException(status_code=404, detail="No se encontraron facturas")

    data = {
        "count": len(invoices),
        "invoices": [inv.to_dict() for inv in invoices]
    }

    result = webhook_sender.trigger_event(db, payload.event, data, org_id=org_id)
    return {"status": "sent", "result": result}

@app.get("/statistics")
async def get_statistics(user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Obtener estadísticas enfocadas en el procesamiento IA y eficiencia operativa"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)

    # Intentar obtener del caché primero
    cache_key = f"stats:dashboard:{org_id}"
    cached_stats = cache_get(cache_key)
    if cached_stats:
        logger.info("⚡ Estadísticas servidas desde caché Redis")
        return cached_stats

    logger.info("📊 Calculando estadísticas operativas desde PostgreSQL...")
    from models import IS_HEROKU

    # --- 1. Estado de la Cola ---
    base_query = db.query(Invoice).filter(Invoice.organization_id == org_id)
    total_invoices = base_query.count()
    processed_invoices = base_query.filter(Invoice.processed == True).count()
    pending_invoices = total_invoices - processed_invoices
    
    # --- 2. Rendimiento Diario (Procesadas Hoy) ---
    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    
    daily_processed_count = base_query.filter(
        Invoice.processed == True,
        Invoice.updated_at >= today_start
    ).count()

    # --- 3. Calidad de IA (Confianza Promedio) ---
    avg_confidence = db.query(func.avg(Invoice.confidence_score)).filter(
        Invoice.processed == True,
        Invoice.confidence_score.isnot(None),
        Invoice.organization_id == org_id
    ).scalar() or 0.0

    # --- 4. Auditoría y Alertas ---
    if IS_HEROKU:
        # Postgres
        audit_alert_count = base_query.filter(
            Invoice.processed == True,
            Invoice.audit_flags != '[]',
            Invoice.audit_flags.isnot(None)
        ).count()
        
        # Get raw audit flags for breakdown
        alert_invoices = base_query.with_entities(Invoice.audit_flags).filter(
            Invoice.processed == True,
            Invoice.audit_flags != '[]',
            Invoice.audit_flags.isnot(None)
        ).all()
    else:
        # SQLite
        audit_alert_count = base_query.filter(
            Invoice.processed == True,
            Invoice.audit_flags != '[]',
            Invoice.audit_flags.isnot(None)
        ).count()
        
        # Get raw audit flags for breakdown
        alert_invoices = base_query.with_entities(Invoice.audit_flags).filter(
            Invoice.processed == True,
            Invoice.audit_flags != '[]',
            Invoice.audit_flags.isnot(None)
        ).all()

    # Calculate Alert Breakdown
    alert_breakdown = {}
    for (flags_json,) in alert_invoices:
        try:
            if flags_json:
                flags = json.loads(flags_json)
                if isinstance(flags, list):
                    for flag in flags:
                        # Categorize flags
                        category = "Otros"
                        flag_lower = flag.lower()
                        if "fiscal" in flag_lower or "tax" in flag_lower:
                            category = "Datos Fiscales"
                        elif "duplicado" in flag_lower:
                            category = "Duplicados"
                        elif "antigua" in flag_lower or "fecha" in flag_lower:
                            category = "Antigüedad"
                        elif "legible" in flag_lower:
                            category = "Legibilidad"
                        elif "impuestos" in flag_lower:
                            category = "Impuestos"
                        
                        alert_breakdown[category] = alert_breakdown.get(category, 0) + 1
        except:
            continue
            
    # Format breakdown for Chart.js
    audit_distribution = {
        "labels": list(alert_breakdown.keys()),
        "data": list(alert_breakdown.values())
    }

    # --- 5. Historial de Volumen de Procesamiento (Últimos 7 días) ---
    if IS_HEROKU:
        daily_volume_data = db.query(
            func.to_char(Invoice.updated_at, 'YYYY-MM-DD').label('day'),
            func.count(Invoice.id).label('count')
        ).filter(
            Invoice.processed == True,
            Invoice.updated_at >= datetime.now() - timedelta(days=7),
            Invoice.organization_id == org_id
        ).group_by(
            func.to_char(Invoice.updated_at, 'YYYY-MM-DD')
        ).order_by(
            func.to_char(Invoice.updated_at, 'YYYY-MM-DD')
        ).all()
    else:
        daily_volume_data = db.query(
            func.strftime('%Y-%m-%d', Invoice.updated_at).label('day'),
            func.count(Invoice.id).label('count')
        ).filter(
            Invoice.processed == True,
            Invoice.updated_at >= datetime.now() - timedelta(days=7),
            Invoice.organization_id == org_id
        ).group_by(
            func.strftime('%Y-%m-%d', Invoice.updated_at)
        ).order_by(
            func.strftime('%Y-%m-%d', Invoice.updated_at)
        ).all()
    
    processing_history = [{'date': d, 'count': c} for d, c in daily_volume_data]

    # --- 6. Costos OpenAI (Reutilizamos servicio existente) ---
    cost_stats = cost_control.get_cost_statistics(db, org_id=org_id)
    
    # Calcular promedio costo por documento
    avg_cost_per_doc = 0.0
    if processed_invoices > 0:
         avg_cost_per_doc = cost_stats.get('total_cost', 0) / processed_invoices

    # --- 7. Invoices Recientes con Alertas (para la tabla de Insights) ---
    recent_alerts_query = base_query.filter(
        Invoice.processed == True,
        Invoice.audit_flags != '[]',
        Invoice.audit_flags.isnot(None)
    ).order_by(Invoice.updated_at.desc()).limit(10).all()
    
    recent_alerts = [inv.to_dict() for inv in recent_alerts_query]

    # Estructurar respuesta para el Dashboard Operativo
    stats_data = {
        'queue': {
            'pending': pending_invoices,
            'processed_total': processed_invoices,
            'total': total_invoices
        },
        'performance': {
            'daily_processed': daily_processed_count,
            'avg_confidence': float(avg_confidence),
            'avg_processing_time': 0, 
            'success_rate': (processed_invoices / total_invoices * 100) if total_invoices > 0 else 0
        },
        'audit': {
            'alerts_count': audit_alert_count,
            'clean_count': processed_invoices - audit_alert_count,
            'recent_alerts': recent_alerts,
            'distribution': audit_distribution
        },
        'costs': {
            'avg_cost_per_doc': float(avg_cost_per_doc),
            'total_tokens': cost_stats.get('total_tokens', 0),
            'total_cost': cost_stats.get('total_cost', 0),
            'model_breakdown': cost_stats.get('model_breakdown', [])
        },
        'charts': {
            'volume_history': processing_history
        },
        'general': {
            'pending_invoices': pending_invoices
        }
    }
    
    # Guardar en caché por 5 minutos
    cache_set(cache_key, stats_data, ttl=300)
    logger.info("💾 Estadísticas operativas guardadas en caché Redis")

    # Notificar actualización via WebSocket
    await websocket_manager.notify_statistics_update(stats_data, org_id)

    return stats_data
@app.get("/categories")
async def get_categories(user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Obtener lista de categorías únicas"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    categories = db.query(Invoice.category).filter(
        Invoice.category.isnot(None),
        Invoice.organization_id == org_id
    ).distinct().all()
    
    return [cat[0] for cat in categories if cat[0]]

@app.get("/export/csv")
async def export_invoices_csv(
    transaction_type: Optional[str] = None,
    category: Optional[str] = None,
    format: Optional[str] = None,
    invoice_ids: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_from_cookie),
    db: Session = Depends(get_db)
):
    """Exportar facturas a CSV con filtros opcionales"""
    try:
        import csv
        import io
        from fastapi.responses import StreamingResponse

        if not user:
            raise HTTPException(status_code=401, detail="No autorizado")
        org_id = get_org_id(user, db)
        
        # Construir query con filtros
        query = db.query(Invoice).filter(Invoice.organization_id == org_id)
        
        if transaction_type:
            query = query.filter(Invoice.transaction_type == transaction_type)
        
        if category:
            query = query.filter(Invoice.category == category)
        if invoice_ids:
            ids = [int(x) for x in invoice_ids.split(',') if x.strip().isdigit()]
            if ids:
                query = query.filter(Invoice.id.in_(ids))
        
        invoices = query.order_by(desc(Invoice.created_at)).all()
        
        # Timestamp para nombres de archivo
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # Exportar formatos especiales
        if format == "dgii_606":
            org = db.query(Organization).filter(Organization.id == org_id).first()
            report_rnc = org.tax_id if org else None
            output = export_service.export_dgii_606(invoices, report_rnc=report_rnc)
            filename = f"dgii_606_{timestamp}.xls"
            return StreamingResponse(
                io.BytesIO(output),
                media_type="application/vnd.ms-excel",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        if format == "quickbooks_bills":
            output = export_service.export_quickbooks_bills(invoices)
            filename = f"quickbooks_bills_{timestamp}.csv"
            return StreamingResponse(
                io.StringIO(output),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        if format == "xero":
            output = export_service.export_xero_bills(invoices)
            filename = f"xero_bills_{timestamp}.csv"
            return StreamingResponse(
                io.StringIO(output),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        if format == "odoo":
            output = export_service.export_odoo_vendor_bills(invoices)
            filename = f"odoo_vendor_bills_{timestamp}.csv"
            return StreamingResponse(
                io.StringIO(output),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        if format == "excel":
            output = export_service.export_excel_generic(invoices)
            filename = f"facturas_export_{timestamp}.xlsx"
            return StreamingResponse(
                io.BytesIO(output),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        # Crear CSV en memoria
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Headers
        headers = [
            'ID',
            'Archivo',
            'Proveedor',
            'Número de Factura',
            'Fecha de Factura',
            'Monto Total',
            'Monto de Impuestos',
            'Moneda',
            'Tipo de Transacción',
            'Categoría',
            'Descripción',
            'Procesado',
            'Confianza IA (%)',
            'Tipo de Archivo',
            'Fecha de Creación',
            'Última Actualización'
        ]
        writer.writerow(headers)
        
        # Datos
        for invoice in invoices:
            row = [
                invoice.id,
                invoice.filename or '',
                invoice.vendor_name or '',
                invoice.invoice_number or '',
                invoice.invoice_date.strftime('%Y-%m-%d') if invoice.invoice_date else '',
                invoice.total_amount or '',
                invoice.tax_amount or '',
                invoice.currency or '',
                'Ingreso' if invoice.transaction_type == 'income' else 'Gasto' if invoice.transaction_type == 'expense' else '',
                invoice.category or '',
                invoice.description or '',
                'Sí' if invoice.processed else 'No',
                f"{round((invoice.confidence_score or 0) * 100, 2)}" if invoice.confidence_score else '',
                invoice.file_type or '',
                invoice.created_at.strftime('%Y-%m-%d %H:%M:%S') if invoice.created_at else '',
                invoice.updated_at.strftime('%Y-%m-%d %H:%M:%S') if invoice.updated_at else ''
            ]
            writer.writerow(row)
        
        # Preparar respuesta
        output.seek(0)
        
        # Nombre del archivo con timestamp
        filename = f"facturas_export_{timestamp}.csv"
        
        def iter_csv():
            yield output.getvalue().encode('utf-8-sig')  # BOM para Excel
        
        return StreamingResponse(
            iter_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        print(f"❌ Error exportando CSV: {e}")
        raise HTTPException(status_code=500, detail=f"Error exportando datos: {str(e)}")

# ===========================================
# GESTIÓN DE WEBHOOKS
# ===========================================

class WebhookCreate(BaseModel):
    url: str
    description: Optional[str] = None
    events: List[str]

@app.get("/api/webhooks")
async def get_webhooks(user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Listar webhooks configurados"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    webhooks = db.query(WebhookEndpoint).filter(WebhookEndpoint.organization_id == org_id).all()
    return [wh.to_dict() for wh in webhooks]

@app.post("/api/webhooks")
async def create_webhook(webhook: WebhookCreate, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Crear nuevo webhook"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    new_webhook = WebhookEndpoint(
        url=webhook.url,
        description=webhook.description,
        events=json.dumps(webhook.events),
        is_active=True,
        organization_id=org_id
    )
    db.add(new_webhook)
    db.commit()
    db.refresh(new_webhook)
    return new_webhook.to_dict()

@app.delete("/api/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Eliminar webhook"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id, WebhookEndpoint.organization_id == org_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    
    db.delete(webhook)
    db.commit()
    return {"message": "Webhook eliminado"}

@app.post("/api/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: int, user: Optional[User] = Depends(get_current_user_from_cookie), db: Session = Depends(get_db)):
    """Probar envío de webhook (dispara evento ping)"""
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    org_id = get_org_id(user, db)
    webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id, WebhookEndpoint.organization_id == org_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook no encontrado")
    
    # Disparar evento de prueba
    test_data = {
        "message": "Este es un evento de prueba desde InvoiceFlow", 
        "webhook_id": webhook.id,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Enviamos un evento 'ping'
    result = webhook_sender.trigger_event(db, "ping", test_data, org_id=org_id)
    
    return {"status": "success", "delivery_result": result}

# ===========================================
# MODELOS PARA EVOLUTION API / WHATSAPP
# ===========================================

class EvolutionContact(BaseModel):
    profile: Optional[Dict[str, str]] = None
    wa_id: str

class EvolutionMessage(BaseModel):
    from_: str = Field(alias="from")
    id: str
    timestamp: str
    type: str
    text: Optional[Dict[str, str]] = None
    image: Optional[Dict[str, Any]] = None
    document: Optional[Dict[str, Any]] = None

class EvolutionWebhookValue(BaseModel):
    messaging_product: str = "whatsapp"
    metadata: Dict[str, Any]
    contacts: Optional[List[EvolutionContact]] = None
    messages: Optional[List[EvolutionMessage]] = None

class EvolutionWebhookChange(BaseModel):
    field: str
    value: EvolutionWebhookValue

class EvolutionWebhookEntry(BaseModel):
    id: str
    changes: List[EvolutionWebhookChange]

class EvolutionWebhook(BaseModel):
    object: str = "whatsapp_business_account"
    entry: List[EvolutionWebhookEntry]

# ===========================================
# ENDPOINTS PARA EVOLUTION API
# ===========================================

@app.post("/evolution/webhook")
async def evolution_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Webhook simplificado para recibir mensajes de Evolution API con notificaciones
    """
    try:
        payload = await request.json()
        print(f"📥 Payload recibido en /evolution/webhook: {json.dumps(payload)[:500]}...")
        
        # Procesar webhook con el servicio simplificado
        result = await whatsapp_service.process_webhook(payload, db)
        
        # Enviar notificaciones WebSocket si hay resultados
        if result.get("status") == "success" and result.get("result"):
            invoice_result = result["result"]
            
            if invoice_result.get("status") == "success":
                # Notificar nueva imagen recibida
                invoice_id = invoice_result.get("invoice_id")
                org_id = None
                if invoice_id:
                    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
                    org_id = inv.organization_id if inv else None

                await websocket_manager.notify_new_whatsapp_image(
                    sender_info=invoice_result.get("sender_info", {}),
                    invoice_id=invoice_id,
                    org_id=org_id
                )
                
                # Notificar procesamiento completo con datos detallados
                openai_result = invoice_result.get("openai_result", {})
                
                # Estructurar los datos para la notificación
                notification_result = {
                    "success": openai_result.get("success", False),
                    "data": openai_result.get("data") if openai_result.get("success") else None,
                    "error": openai_result.get("error") if not openai_result.get("success") else None
                }
                
                await websocket_manager.notify_processing_complete(
                    invoice_id=invoice_id,
                    result=notification_result,
                    org_id=org_id
                )
                
                # Verificar alertas de costos
                alerts = cost_control.get_cost_alerts(db, org_id=org_id)
                if alerts["has_alerts"]:
                    for alert in alerts["alerts"]:
                        await websocket_manager.notify_cost_alert(alert, org_id=org_id)
        
        return result
        
    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/evolution/send-message")
async def send_evolution_message(
    instance_name: str,
    phone: str,
    message: str,
    evolution_api_url: Optional[str] = None
):
    """
    Enviar mensaje de respuesta a través de Evolution API
    """
    try:
        # URL por defecto de Evolution API (debe configurarse según tu instalación)
        if not evolution_api_url:
            evolution_api_url = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
        
        # API Key de Evolution (debe configurarse)
        api_key = os.getenv("EVOLUTION_API_KEY", "")
        
        # Preparar datos para envío
        send_data = {
            "number": phone,
            "text": message
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        if api_key:
            headers["apikey"] = api_key
        
        # Enviar mensaje usando Evolution API
        response = requests.post(
            f"{evolution_api_url}/message/sendText/{instance_name}",
            json=send_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return {
                "status": "success",
                "message": "Mensaje enviado exitosamente",
                "response": response.json()
            }
        else:
            return {
                "status": "error",
                "message": f"Error al enviar mensaje: {response.status_code}",
                "details": response.text
            }
            
    except Exception as e:
        print(f"❌ Error enviando mensaje por Evolution API: {e}")
        return {
            "status": "error",
            "error": str(e)
        }

@app.get("/evolution/instance-status/{instance_name}")
async def get_evolution_instance_status(
    instance_name: str,
    evolution_api_url: Optional[str] = None
):
    """
    Verificar estado de una instancia de Evolution API
    """
    try:
        if not evolution_api_url:
            evolution_api_url = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
        
        api_key = os.getenv("EVOLUTION_API_KEY", "")
        
        headers = {}
        if api_key:
            headers["apikey"] = api_key
        
        response = requests.get(
            f"{evolution_api_url}/instance/connectionState/{instance_name}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return {
                "status": "success",
                "instance_status": response.json()
            }
        else:
            return {
                "status": "error",
                "message": f"Error obteniendo estado: {response.status_code}"
            }
            
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

@app.get("/evolution/security-config")
async def get_security_config():
    """
    Verificar configuración de seguridad del sistema
    """
    authorized_number = os.getenv("AUTHORIZED_WHATSAPP_NUMBER", "15555550100")
    
    return {
        "status": "success",
        "security_enabled": True,
        "authorized_number": authorized_number,
        "description": "Solo el número autorizado puede enviar facturas al sistema",
        "note": "Los mensajes de otros números serán automáticamente rechazados"
    }

@app.get("/websocket/status")
async def get_websocket_status():
    """
    Verificar estado de las conexiones WebSocket
    """
    return {
        "status": "success",
        "websocket_status": websocket_manager.get_status(),
        "description": "Estado actual del sistema de notificaciones en tiempo real"
    }

@app.get("/api/redis/stats")
async def get_redis_stats():
    """
    Obtener estadísticas de Redis (caché)
    """
    stats = get_cache_stats()
    return {
        "redis": stats,
        "description": "Estadísticas de rendimiento del sistema de caché Redis"
    }

# --- EVOLUTION API MANAGEMENT ---

def get_setting_value(db, key):
    """Helper para obtener valor de configuración"""
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s else None

@app.get("/evolution/proxy/status")
async def get_evolution_status(db: Session = Depends(get_db)):
    """Obtener estado de la instancia configurada"""
    url = get_setting_value(db, "evolution_url") or os.getenv("EVOLUTION_API_URL")
    apikey = get_setting_value(db, "evolution_apikey") or os.getenv("EVOLUTION_API_KEY")
    instance = get_setting_value(db, "evolution_instance") or os.getenv("EVOLUTION_INSTANCE_NAME")
    
    if not url or not apikey or not instance:
        return {"status": "not_configured"}
        
    try:
        headers = {"apikey": apikey}
        # Evolution v2 endpoint
        res = requests.get(f"{url}/instance/connectionState/{instance}", headers=headers, timeout=5)
        if res.status_code == 200:
            return res.json()
        elif res.status_code == 404:
            return {"status": "instance_not_found"}
        else:
            return {"status": "error", "code": res.status_code}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/evolution/proxy/qr")
async def get_evolution_qr(db: Session = Depends(get_db)):
    """Obtener QR code para escanear"""
    url = get_setting_value(db, "evolution_url") or os.getenv("EVOLUTION_API_URL")
    apikey = get_setting_value(db, "evolution_apikey") or os.getenv("EVOLUTION_API_KEY")
    instance = get_setting_value(db, "evolution_instance") or os.getenv("EVOLUTION_INSTANCE_NAME")
    
    if not url or not apikey: return {"error": "Configuración incompleta"}
    
    try:
        headers = {"apikey": apikey}
        # Conectar endpoint de Evolution para conectar
        res = requests.get(f"{url}/instance/connect/{instance}", headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            return data
        else:
            return {"error": "Error obteniendo QR", "details": res.text}
    except Exception as e:
        return {"error": str(e)}

@app.post("/evolution/proxy/create")
async def create_evolution_instance(db: Session = Depends(get_db)):
    """Crear instancia si no existe"""
    url = get_setting_value(db, "evolution_url") or os.getenv("EVOLUTION_API_URL")
    apikey = get_setting_value(db, "evolution_apikey") or os.getenv("EVOLUTION_API_KEY")
    instance = get_setting_value(db, "evolution_instance") or os.getenv("EVOLUTION_INSTANCE_NAME")
    
    instance_token = os.getenv("EVOLUTION_INSTANCE_TOKEN")
    if not instance_token:
        return {"error": "EVOLUTION_INSTANCE_TOKEN no configurado"}

    payload = {
        "instanceName": instance,
        "token": instance_token,
        "qrcode": True
    }
    
    try:
        headers = {"apikey": apikey, "Content-Type": "application/json"}
        res = requests.post(f"{url}/instance/create", json=payload, headers=headers)
        return res.json()
    except Exception as e:
        return {"error": str(e)}

@app.post("/evolution/test-get-base64")
async def test_evolution_get_base64(
    message_id: str,
    instance_name: Optional[str] = None
):
    """
    Endpoint de prueba para verificar la funcionalidad getBase64FromMediaMessage
    """
    try:
        if not instance_name:
            instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "your_instance")
        
        print(f"🧪 Testing getBase64FromMediaMessage para mensaje: {message_id}")
        
        # Intentar obtener base64 desde Evolution API
        base64_result = await get_base64_from_evolution_api(
            message_key_id=message_id,
            instance_name=instance_name
        )
        
        if base64_result:
            # Validar el base64
            try:
                import base64
                decoded = base64.b64decode(base64_result)
                
                return {
                    "status": "success",
                    "message": "Base64 obtenido exitosamente",
                    "base64_length": len(base64_result),
                    "decoded_bytes": len(decoded),
                    "preview": base64_result[:100] + "..." if len(base64_result) > 100 else base64_result,
                    "instance_used": instance_name
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": "Base64 obtenido pero inválido",
                    "error": str(e),
                    "raw_response": base64_result[:200] + "..." if len(base64_result) > 200 else base64_result
                }
        else:
            return {
                "status": "error",
                "message": "No se pudo obtener base64 desde Evolution API",
                "message_id": message_id,
                "instance_name": instance_name
            }
            
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error en endpoint de prueba: {str(e)}",
            "message_id": message_id
        }

# ===========================================
# FUNCIONES AUXILIARES PARA EVOLUTION API
# ===========================================

async def process_evolution_message(
    message: EvolutionMessage,
    sender_phone: str,
    sender_name: str,
    db: Session
) -> Optional[dict]:
    """
    Procesa un mensaje recibido de Evolution API
    """
    try:
        # Solo procesar imágenes por ahora
        if message.type == "image" and message.image:
            print(f"📸 Procesando imagen de {sender_name} ({sender_phone})")
            
            # En Evolution API, la imagen puede venir con URL directa o media_id
            media_url = None
            
            # Intentar obtener URL de diferentes formas
            if isinstance(message.image, dict):
                media_url = message.image.get("url") or message.image.get("link")
                
                # Si no hay URL directa, usar media_id para descargar
                if not media_url and message.image.get("id"):
                    media_url = await get_evolution_media_url(message.image["id"])
            
            if media_url:
                result = await process_whatsapp_image_from_evolution(
                    phone=sender_phone,
                    media_url=media_url,
                    message_id=message.id,
                    sender_name=sender_name,
                    db=db
                )
                
                # Enviar respuesta automática
                if result and result.get("status") == "success":
                    invoice_id = result.get("invoice_id")
                    await send_auto_response(
                        phone=sender_phone,
                        invoice_id=invoice_id,
                        success=True
                    )
                
                return result
            else:
                print(f"⚠️ No se pudo obtener URL de imagen para mensaje {message.id}")
                return None
        
        # Procesar mensajes de texto (opcional - para comandos)
        elif message.type == "text" and message.text:
            text_content = message.text.get("body", "").lower().strip()
            
            # Comandos básicos
            if text_content in ["estado", "status", "help", "ayuda"]:
                await send_help_message(sender_phone)
                return {
                    "status": "help_sent",
                    "message": "Mensaje de ayuda enviado"
                }
        
        return None
        
    except Exception as e:
        print(f"❌ Error procesando mensaje de Evolution: {e}")
        return {"status": "error", "error": str(e)}

async def download_and_process_whatsapp_image(
    url: str,
    phone: str,
    message_id: str,
    sender_name: str,
    db: Session
) -> dict:
    """
    Descarga y procesa una imagen directamente desde la URL de WhatsApp
    """
    try:
        print(f"🌐 Descargando imagen desde URL: {url[:50]}...")
        
        # Descargar la imagen
        import requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; WhatsApp/2.0)'
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        image_data = response.content
        print(f"✅ Imagen descargada: {len(image_data)} bytes")
        
        # Validar tamaño mínimo
        if len(image_data) < 1024:
            print(f"❌ Imagen descargada muy pequeña ({len(image_data)} bytes)")
            return {"status": "error", "error": f"Imagen muy pequeña: {len(image_data)} bytes"}
        
        # Procesar y convertir imagen
        try:
            from PIL import Image
            import io
            
            # Intentar abrir la imagen
            image_buffer = io.BytesIO(image_data)
            with Image.open(image_buffer) as img:
                original_format = img.format.lower() if img.format else 'unknown'
                print(f"🔍 Formato descargado: {original_format}")
                
                # Convertir a JPEG para compatibilidad
                print(f"🔄 Convirtiendo a JPEG para OpenAI")
                
                # Convertir a RGB si es necesario
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Redimensionar si es muy grande
                max_size = 2048
                if img.width > max_size or img.height > max_size:
                    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                    print(f"📐 Imagen redimensionada a {img.width}x{img.height}")
                
                # Guardar como JPEG
                output_buffer = io.BytesIO()
                img.save(output_buffer, format='JPEG', quality=90, optimize=True)
                image_data = output_buffer.getvalue()
                
                print(f"✅ Imagen procesada: {len(image_data)} bytes")
                
        except Exception as img_error:
            print(f"❌ Error procesando imagen descargada: {img_error}")
            return {"status": "error", "error": f"Error procesando imagen: {str(img_error)}"}
        
        # Guardar imagen localmente
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        phone_clean = phone.replace("@s.whatsapp.net", "").replace("@c.us", "")
        filename = f"whatsapp_{phone_clean}_{timestamp}_url.jpg"
        file_path = os.path.join("uploads", filename)
        
        # Crear directorio si no existe
        os.makedirs("uploads", exist_ok=True)
        
        with open(file_path, "wb") as f:
            f.write(image_data)
        
        # Crear registro en base de datos
        invoice = Invoice(
            filename=filename,
            file_path=file_path,
            file_type="image",
            processed=False,
            description=f"Factura descargada de WhatsApp URL de {sender_name} ({phone})",
            organization_id=get_default_org(db).id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        
        print(f"💾 Imagen guardada desde URL: {filename} (ID: {invoice.id})")
        
        # Procesar automáticamente con OpenAI
        try:
            extracted_data = openai_processor.process_invoice(file_path, "image")
            
            if "error" not in extracted_data:
                # Actualizar factura con datos extraídos
                for key, value in extracted_data.items():
                    if hasattr(invoice, key) and value is not None:
                        setattr(invoice, key, value)
                
                invoice.processed = True
                db.commit()
                
                print(f"✅ Factura procesada exitosamente: ID {invoice.id}")
                return {
                    "status": "success", 
                    "invoice_id": invoice.id,
                    "filename": filename,
                    "data": extracted_data
                }
            else:
                print(f"⚠️ Error en OpenAI: {extracted_data.get('error', 'Unknown error')}")
                return {
                    "status": "partial_success",
                    "invoice_id": invoice.id,
                    "filename": filename,
                    "error": extracted_data.get('error')
                }
                
        except Exception as e:
            print(f"❌ Error procesando con OpenAI: {e}")
            return {
                "status": "partial_success",
                "invoice_id": invoice.id,
                "filename": filename,
                "error": f"Error OpenAI: {str(e)}"
            }
            
    except Exception as e:
        print(f"❌ Error descargando imagen desde URL: {e}")
        return {"status": "error", "error": f"Error descargando imagen: {str(e)}"}

async def process_whatsapp_image_from_base64(
    phone: str,
    image_base64: str,
    message_id: str,
    sender_name: str,
    db: Session,
    source_note: str = "WhatsApp webhook"
) -> dict:
    """
    Procesa una imagen recibida por WhatsApp desde datos base64
    """
    try:
        print(f"📱 Procesando imagen base64 de WhatsApp: {phone}")
        
        # Decodificar imagen base64
        try:
            import base64
            image_data = base64.b64decode(image_base64)
            print(f"✅ Imagen decodificada: {len(image_data)} bytes")
        except Exception as e:
            print(f"❌ Error decodificando base64: {e}")
            return {"status": "error", "error": f"Error decodificando imagen: {str(e)}"}
        
        # Validar que la imagen tenga un tamaño mínimo
        if len(image_data) < 100:
            print(f"❌ Imagen extremadamente pequeña ({len(image_data)} bytes), datos corruptos")
            return {"status": "error", "error": f"Imagen corrupta: {len(image_data)} bytes"}
        
        print(f"✅ Imagen de calidad recibida: {len(image_data)} bytes")
        
        # Procesar y convertir imagen de forma robusta
        try:
            from PIL import Image
            import io
            
            # Múltiples intentos de procesamiento de imagen
            image_buffer = io.BytesIO(image_data)
            processed_successfully = False
            
            # Intento 1: Procesar directamente
            try:
                with Image.open(image_buffer) as img:
                    image_format = img.format.lower() if img.format else 'unknown'
                    print(f"🔍 Formato detectado: {image_format} ({img.width}x{img.height})")
                    
                                    # Verificar resolución de imagen
                print(f"📐 Resolución de imagen: {img.width}x{img.height}")
                
                # Si la imagen es muy pequeña, escalarla para mejor OCR
                if img.width < 400 or img.height < 400:
                    print(f"🔍 Imagen pequeña detectada, mejorando resolución para OCR")
                    scale_factor = max(400 / img.width, 400 / img.height)
                    new_size = (int(img.width * scale_factor), int(img.height * scale_factor))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                    print(f"📐 Imagen escalada a {img.width}x{img.height}")
                    
                    # Convertir a RGB si es necesario
                    if img.mode != 'RGB':
                        print(f"🎨 Convirtiendo modo {img.mode} → RGB")
                        img = img.convert('RGB')
                    
                    # Guardar como JPEG de alta calidad
                    output_buffer = io.BytesIO()
                    img.save(output_buffer, format='JPEG', quality=95, optimize=True)
                    image_data = output_buffer.getvalue()
                    image_format = 'jpeg'
                    
                    print(f"✅ Imagen procesada exitosamente: {len(image_data)} bytes")
                    processed_successfully = True
                    
            except Exception as img_error:
                print(f"⚠️ Intento directo falló: {img_error}")
                
                # Intento 2: Verificar si son datos crudos o necesitan preprocesamiento
                try:
                    # Intentar detectar si hay headers o metadatos extras
                    if image_data.startswith(b'\xff\xd8\xff'):  # JPEG header
                        print("🔍 Detectados headers JPEG válidos")
                    elif image_data.startswith(b'\x89PNG'):  # PNG header
                        print("🔍 Detectados headers PNG válidos")
                    else:
                        print(f"🔍 Headers no reconocidos: {image_data[:10].hex()}")
                    
                    # Intentar con verificación deshabilitada
                    from PIL import ImageFile
                    ImageFile.LOAD_TRUNCATED_IMAGES = True
                    
                    image_buffer = io.BytesIO(image_data)
                    with Image.open(image_buffer) as img:
                        img.load()  # Forzar carga completa
                        print(f"✅ Segunda carga exitosa: {img.format} {img.width}x{img.height}")
                        
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        output_buffer = io.BytesIO()
                        img.save(output_buffer, format='JPEG', quality=95)
                        image_data = output_buffer.getvalue()
                        image_format = 'jpeg'
                        
                        processed_successfully = True
                        print(f"✅ Imagen recuperada: {len(image_data)} bytes")
                        
                except Exception as second_error:
                    print(f"❌ Segundo intento también falló: {second_error}")
            
            if not processed_successfully:
                return {"status": "error", "error": f"No se pudo procesar la imagen tras múltiples intentos"}
            
            # Imagen procesada exitosamente
            print(f"✅ Imagen procesada y lista para OCR: {len(image_data)} bytes")
                    
        except Exception as e:
            print(f"❌ Error general procesando imagen: {e}")
            return {"status": "error", "error": f"Error procesando imagen: {str(e)}"}
        
        # Guardar imagen localmente
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        phone_clean = phone.replace("@s.whatsapp.net", "").replace("@c.us", "")
        extension = 'jpg' if image_format == 'jpeg' else image_format
        filename = f"whatsapp_{phone_clean}_{timestamp}.{extension}"
        file_path = os.path.join("uploads", filename)
        
        # Crear directorio si no existe
        os.makedirs("uploads", exist_ok=True)
        
        with open(file_path, "wb") as f:
            f.write(image_data)
        
        # Crear registro en base de datos
        invoice = Invoice(
            filename=filename,
            file_path=file_path,
            file_type="image",
            processed=False,
            description=f"Factura recibida por WhatsApp de {sender_name} ({phone}) - {source_note}",
            organization_id=get_default_org(db).id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        
        print(f"💾 Imagen guardada: {filename} (ID: {invoice.id}) - Fuente: {source_note}")
        
        # Procesar automáticamente con OpenAI
        try:
            extracted_data = openai_processor.process_invoice(file_path, "image")
            
            if extracted_data:
                # Actualizar invoice con datos extraídos
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
                invoice.confidence_score = extracted_data.get('confidence')
                invoice.goods_services_type = extracted_data.get('goods_services_type')
                invoice.raw_extracted_data = json.dumps(extracted_data)
                invoice.processed = True
                
                db.commit()
                db.refresh(invoice)
                
                print(f"✅ Factura procesada exitosamente: ID {invoice.id}")
                
                return {
                    "status": "success",
                    "message": "Imagen procesada exitosamente",
                    "invoice_id": invoice.id,
                    "extracted_data": extracted_data,
                    "whatsapp_info": {
                        "phone": phone,
                        "sender_name": sender_name,
                        "message_id": message_id
                    }
                }
            else:
                print(f"⚠️ No se pudieron extraer datos de la imagen")
                return {
                    "status": "partial_success",
                    "message": "Imagen guardada pero no se pudieron extraer datos",
                    "invoice_id": invoice.id,
                    "whatsapp_info": {
                        "phone": phone,
                        "sender_name": sender_name,
                        "message_id": message_id
                    }
                }
                
        except Exception as openai_error:
            print(f"❌ Error en OpenAI: {openai_error}")
            return {
                "status": "partial_success",
                "message": f"Imagen guardada pero error en procesamiento: {str(openai_error)}",
                "invoice_id": invoice.id,
                "whatsapp_info": {
                    "phone": phone,
                    "sender_name": sender_name,
                    "message_id": message_id
                }
            }
            
    except Exception as e:
        print(f"❌ Error procesando imagen base64 de WhatsApp: {e}")
        return {"status": "error", "error": str(e)}

async def process_whatsapp_image_from_evolution(
    phone: str,
    media_url: str,
    message_id: str,
    sender_name: str,
    db: Session
) -> dict:
    """
    Procesa una imagen recibida por WhatsApp desde Evolution API
    """
    try:
        print(f"📱 Descargando imagen de WhatsApp: {phone} -> {media_url}")
        
        # Descargar imagen
        image_data = await download_media_from_url(media_url)
        if not image_data:
            raise HTTPException(status_code=400, detail="No se pudo descargar la imagen")
        
        # Guardar imagen localmente
        file_extension = get_file_extension_from_url(media_url)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"whatsapp_{phone}_{timestamp}{file_extension}"
        file_path = os.path.join("uploads", filename)
        
        # Crear directorio si no existe
        os.makedirs("uploads", exist_ok=True)
        
        with open(file_path, "wb") as f:
            f.write(image_data)
        
        # Crear registro en base de datos
        invoice = Invoice(
            filename=filename,
            file_path=file_path,
            file_type="image",
            processed=False,
            description=f"Factura recibida por WhatsApp de {sender_name} ({phone})",
            organization_id=get_default_org(db).id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        
        print(f"💾 Imagen guardada: {filename} (ID: {invoice.id})")
        
        # Procesar automáticamente con OpenAI
        try:
            extracted_data = openai_processor.process_invoice(file_path, "image")
            
            if extracted_data:
                # Actualizar invoice con datos extraídos
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
                invoice.confidence_score = extracted_data.get('confidence')
                invoice.goods_services_type = extracted_data.get('goods_services_type')
                invoice.raw_extracted_data = json.dumps(extracted_data)
                invoice.processed = True
                
                db.commit()
                db.refresh(invoice)
                
                print(f"✅ Factura procesada exitosamente: ID {invoice.id}")
                
                return {
                    "status": "success",
                    "message": "Imagen procesada exitosamente",
                    "invoice_id": invoice.id,
                    "extracted_data": extracted_data,
                    "whatsapp_info": {
                        "phone": phone,
                        "sender_name": sender_name,
                        "message_id": message_id
                    }
                }
            else:
                print(f"⚠️ No se pudieron extraer datos de la imagen")
                return {
                    "status": "partial_success",
                    "message": "Imagen guardada pero no se pudieron extraer datos",
                    "invoice_id": invoice.id,
                    "whatsapp_info": {
                        "phone": phone,
                        "sender_name": sender_name,
                        "message_id": message_id
                    }
                }
                
        except Exception as openai_error:
            print(f"❌ Error en OpenAI: {openai_error}")
            return {
                "status": "partial_success",
                "message": f"Imagen guardada pero error en procesamiento: {str(openai_error)}",
                "invoice_id": invoice.id,
                "whatsapp_info": {
                    "phone": phone,
                    "sender_name": sender_name,
                    "message_id": message_id
                }
            }
            
    except Exception as e:
        print(f"❌ Error procesando imagen de WhatsApp: {e}")
        return {"status": "error", "error": str(e)}

async def download_media_from_url(media_url: str) -> Optional[bytes]:
    """
    Descarga media desde una URL - para Evolution API usa el endpoint de descarga
    """
    try:
        # Si es una URL de WhatsApp, usar Evolution API para descargar
        if "whatsapp.net" in media_url or "mmg.whatsapp.net" in media_url:
            return await download_media_from_evolution_api(media_url)
        
        # Para otras URLs, descarga directa
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(media_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        return response.content
        
    except Exception as e:
        print(f"❌ Error descargando media: {e}")
        return None

async def download_media_from_evolution_api(media_url: str) -> Optional[bytes]:
    """
    Descarga media usando Evolution API - probando diferentes endpoints
    """
    try:
        evolution_api_url = os.getenv("EVOLUTION_API_URL", "https://your-evolution-api.example.com")
        api_key = os.getenv("EVOLUTION_API_KEY", "YOUR_EVOLUTION_API_KEY")
        instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "your_instance")
        
        if not evolution_api_url or not api_key:
            print("❌ Evolution API URL o API Key no configurados")
            return None
        
        headers = {
            "apikey": api_key,
            "Content-Type": "application/json"
        }
        
        print(f"📥 Descargando media via Evolution API: {media_url[:50]}...")
        
        # Intentar diferentes endpoints para descargar media
        endpoints_to_try = [
            f"/media/download/{instance_name}",
            f"/instance/download/{instance_name}",
            f"/message/media/{instance_name}",
            f"/whatsapp/media/{instance_name}"
        ]
        
        for endpoint in endpoints_to_try:
            try:
                # Payload con la URL de la media
                download_data = {
                    "url": media_url,
                    "message": {
                        "url": media_url
                    }
                }
                
                print(f"🔄 Probando endpoint: {endpoint}")
                
                response = requests.post(
                    f"{evolution_api_url}{endpoint}",
                    json=download_data,
                    headers=headers,
                    timeout=15
                )
                
                if response.status_code == 200:
                    # Evolution API puede devolver la media en base64 o como bytes
                    try:
                        result = response.json()
                        if "media" in result:
                            # Si viene en base64, decodificar
                            if isinstance(result["media"], str):
                                import base64
                                return base64.b64decode(result["media"])
                            else:
                                return result["media"]
                        elif "data" in result:
                            # Otro formato posible
                            if isinstance(result["data"], str):
                                import base64
                                return base64.b64decode(result["data"])
                            else:
                                return result["data"]
                    except:
                        # Si no es JSON, devolver contenido raw
                        return response.content
                        
                print(f"⚠️ Endpoint {endpoint} falló: {response.status_code}")
                
            except Exception as e:
                print(f"⚠️ Error con endpoint {endpoint}: {e}")
                continue
        
        # Si todos los endpoints fallan, intentar descarga directa con headers adicionales
        print("🔄 Intentando descarga directa con headers especiales...")
        direct_headers = {
            'User-Agent': 'WhatsApp/2.23.20.0',
            'Accept': 'image/*,*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://web.whatsapp.com/'
        }
        
        response = requests.get(media_url, headers=direct_headers, timeout=30)
        if response.status_code == 200:
            print("✅ Descarga directa exitosa")
            return response.content
        else:
            print(f"❌ Descarga directa falló: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Error descargando media con Evolution API: {e}")
        return None

async def get_evolution_media_url(media_id: str) -> Optional[str]:
    """
    Obtiene URL de media usando media_id de Evolution API
    """
    try:
        evolution_api_url = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
        api_key = os.getenv("EVOLUTION_API_KEY", "")
        
        headers = {}
        if api_key:
            headers["apikey"] = api_key
        
        # Intentar obtener URL del media
        response = requests.get(
            f"{evolution_api_url}/chat/getMedia/{media_id}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get("media_url") or data.get("url")
        
        return None
        
    except Exception as e:
        print(f"❌ Error obteniendo URL de media: {e}")
        return None

async def get_base64_from_evolution_api(message_key_id: str, instance_name: str = None) -> Optional[str]:
    """
    Obtiene el base64 de una imagen usando Evolution API getBase64FromMediaMessage
    Útil cuando el thumbnail del webhook es muy pequeño
    """
    try:
        evolution_api_url = os.getenv("EVOLUTION_API_URL", "https://your-evolution-api.example.com")
        api_key = os.getenv("EVOLUTION_API_KEY", "YOUR_EVOLUTION_API_KEY")
        
        if not instance_name:
            instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "your_instance")
        
        if not evolution_api_url or not api_key or not instance_name:
            print("❌ Evolution API URL, API Key o Instance Name no configurados")
            return None
        
        headers = {
            "apikey": api_key,
            "Content-Type": "application/json"
        }
        
        # Payload con el message key id
        payload = {
            "message": {
                "key": {
                    "id": message_key_id
                }
            },
            "convertToMp4": False  # Solo queremos imágenes, no video
        }
        
        print(f"🔄 Obteniendo base64 original desde Evolution API para mensaje: {message_key_id}")
        
        # Usar el endpoint específico para obtener base64
        endpoint = f"/chat/getBase64FromMediaMessage/{instance_name}"
        full_url = f"{evolution_api_url}{endpoint}"
        
        print(f"📡 Request: POST {full_url}")
        print(f"📋 Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(
            full_url,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        print(f"📊 Response status: {response.status_code}")
        
        if response.status_code in [200, 201]:  # Aceptar tanto 200 (OK) como 201 (Created)
            result = response.json()
            print(f"✅ Respuesta Evolution API recibida: {type(result)}")
            
            # Evolution API puede devolver el base64 en diferentes campos
            base64_data = None
            
            if isinstance(result, dict):
                # Buscar base64 en diferentes posibles campos (priorizando 'base64' que es lo que devuelve Evolution API)
                possible_fields = ['base64', 'mediaBase64', 'media', 'data', 'content']
                
                for field in possible_fields:
                    if field in result and result[field]:
                        base64_data = result[field]
                        print(f"📸 Base64 encontrado en campo: {field}")
                        break
                
                # También revisar si está anidado
                if not base64_data and 'message' in result:
                    message_data = result['message']
                    for field in possible_fields:
                        if field in message_data and message_data[field]:
                            base64_data = message_data[field]
                            print(f"📸 Base64 encontrado en message.{field}")
                            break
            
            elif isinstance(result, str):
                # Si la respuesta directa es string, asumir que es base64
                base64_data = result
                print("📸 Respuesta directa es string, asumiendo base64")
            
            if base64_data:
                # Verificar que es base64 válido
                try:
                    import base64
                    decoded = base64.b64decode(base64_data)
                    print(f"✅ Base64 válido obtenido: {len(decoded)} bytes")
                    return base64_data
                except Exception as e:
                    print(f"❌ Base64 inválido: {e}")
                    return None
            else:
                print("❌ No se encontró campo base64 en la respuesta")
                print(f"📋 Campos disponibles: {list(result.keys()) if isinstance(result, dict) else 'response is not dict'}")
                return None
        
        else:
            print(f"❌ Error en Evolution API: {response.status_code}")
            try:
                error_details = response.json()
                print(f"📋 Detalles del error: {json.dumps(error_details, indent=2)}")
            except:
                print(f"📋 Error response (text): {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Error obteniendo base64 desde Evolution API: {e}")
        return None

def get_file_extension_from_url(url: str) -> str:
    """
    Obtiene extensión de archivo desde URL
    """
    try:
        path = url.split('?')[0].split('#')[0]
        ext = os.path.splitext(path)[1].lower()
        
        if ext in ALLOWED_IMAGE_EXTENSIONS:
            return ext
        else:
            return '.jpg'  # Default
    except:
        return '.jpg'

async def send_auto_response(phone: str, invoice_id: Optional[int] = None, success: bool = True, error_type: str = None):
    """
    Envía respuesta automática por WhatsApp
    """
    try:
        instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "default")
        
        if success and invoice_id:
            message = f"✅ ¡Factura procesada exitosamente!\n\n📄 ID: {invoice_id}\n\nPuedes consultar los detalles en el sistema. ¡Gracias!"
        elif success:
            message = "✅ Imagen recibida correctamente. Procesando factura..."
        else:
            # Diferentes mensajes según el tipo de error
            if error_type == "evolution_api_error":
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
        
        await send_evolution_message(
            instance_name=instance_name,
            phone=phone,
            message=message
        )
        
    except Exception as e:
        print(f"❌ Error enviando respuesta automática: {e}")

async def send_help_message(phone: str):
    """
    Envía mensaje de ayuda
    """
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
    
    try:
        instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "default")
        await send_evolution_message(
            instance_name=instance_name,
            phone=phone,
            message=help_text
        )
    except Exception as e:
        print(f"❌ Error enviando mensaje de ayuda: {e}")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port) 
