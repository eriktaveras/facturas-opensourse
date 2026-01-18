# 📱 Integración con Evolution API

Este documento explica cómo configurar la integración directa con Evolution API para recibir y procesar facturas por WhatsApp.

## 🚀 Características Implementadas

### ✅ Funcionalidades Principales
- **Webhook automático** para recibir mensajes de WhatsApp
- **Procesamiento automático** de imágenes de facturas con OpenAI
- **Respuestas automáticas** al usuario
- **Comandos de texto** (ayuda, estado)
- **Gestión de instancias** de Evolution API

### 📋 Endpoints Disponibles

#### Webhook Principal
```
POST /evolution/webhook
```
Recibe mensajes de WhatsApp desde Evolution API y procesa automáticamente las imágenes de facturas.

#### Envío de Mensajes
```
POST /evolution/send-message
```
Envía mensajes de respuesta por WhatsApp usando Evolution API.

#### Estado de Instancia
```
GET /evolution/instance-status/{instance_name}
```
Verifica el estado de conexión de una instancia de Evolution API.

## ⚙️ Configuración

### 1. Variables de Entorno

Agrega estas variables a tu archivo `.env`:

```bash
# Evolution API Configuration
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=tu_clave_de_api_aqui
EVOLUTION_INSTANCE_NAME=mi_instancia

# OpenAI (necesario para procesamiento)
OPENAI_API_KEY=tu_clave_openai_aqui
```

### 2. Configuración de Evolution API

#### Instalar Evolution API

```bash
# Con Docker
docker run -d \
  --name evolution-api \
  -p 8080:8080 \
  -e API_KEY=tu_clave_de_api \
  atendai/evolution-api:latest

# O seguir documentación oficial:
# https://doc.evolution-api.com/v2/en/get-started/install
```

#### Crear Instancia

```bash
curl -X POST http://localhost:8080/instance/create \
  -H "Content-Type: application/json" \
  -H "apikey: tu_clave_de_api" \
  -d '{
    "instanceName": "mi_instancia",
    "qrcode": true
  }'
```

#### Configurar Webhook

```bash
curl -X POST http://localhost:8080/webhook/set/mi_instancia \
  -H "Content-Type: application/json" \
  -H "apikey: tu_clave_de_api" \
  -d '{
    "url": "http://tu-servidor.com:8000/evolution/webhook",
    "enabled": true,
    "events": ["messages"]
  }'
```

### 3. Conectar WhatsApp

1. **Escanear QR Code:**
   ```bash
   curl -X GET http://localhost:8080/instance/qrcode/mi_instancia \
     -H "apikey: tu_clave_de_api"
   ```

2. **Verificar Conexión:**
   ```bash
   curl -X GET http://localhost:8080/instance/connectionState/mi_instancia \
     -H "apikey: tu_clave_de_api"
   ```

## 🔄 Flujo de Funcionamiento

1. **Usuario envía imagen por WhatsApp** → Evolution API
2. **Evolution API** → `POST /evolution/webhook` (nuestro sistema)
3. **Sistema descarga imagen** y la guarda en `uploads/`
4. **OpenAI procesa la imagen** y extrae datos de la factura
5. **Sistema guarda factura** en base de datos
6. **Respuesta automática** enviada al usuario con resultado

## 📱 Comandos de WhatsApp

Los usuarios pueden enviar estos comandos por texto:

- `ayuda` o `help` - Muestra información de ayuda
- `estado` o `status` - Muestra estado del sistema

## 🛠️ Testing

### Verificar Webhook
```bash
curl -X POST http://localhost:8000/evolution/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [{
      "id": "test",
      "changes": [{
        "field": "messages",
        "value": {
          "messaging_product": "whatsapp",
          "metadata": {"phone_number_id": "123"},
          "messages": [{
            "from": "1234567890",
            "id": "test_msg_001",
            "timestamp": "1234567890",
            "type": "text",
            "text": {"body": "ayuda"}
          }],
          "contacts": [{
            "profile": {"name": "Test User"},
            "wa_id": "1234567890"
          }]
        }
      }]
    }]
  }'
```

### Enviar Mensaje de Prueba
```bash
curl -X POST http://localhost:8000/evolution/send-message \
  -H "Content-Type: application/json" \
  -d '{
    "instance_name": "mi_instancia",
    "phone": "1234567890",
    "message": "¡Prueba exitosa del bot de facturas!"
  }'
```

### Verificar Estado de Instancia
```bash
curl -X GET http://localhost:8000/evolution/instance-status/mi_instancia
```

## 🔧 Solución de Problemas

### Error de Conexión a Evolution API
- Verificar que Evolution API esté ejecutándose en el puerto correcto
- Comprobar la variable `EVOLUTION_API_URL` en `.env`
- Validar que la clave API sea correcta

### Webhook No Funciona
- Verificar que el webhook esté configurado correctamente en Evolution API
- Comprobar que el servidor sea accesible desde Evolution API
- Revisar logs para errores de procesamiento

### Imágenes No Se Procesan
- Verificar que OpenAI API Key esté configurada
- Comprobar que las imágenes se descarguen correctamente
- Revisar logs de procesamiento de OpenAI

## 📊 Monitoreo

El sistema incluye logging detallado:

```bash
# Ejecutar con logs visibles
python3 main.py

# Logs incluyen:
# 📱 Webhooks recibidos
# 📸 Procesamiento de imágenes  
# ✅ Respuestas automáticas
# ❌ Errores y excepciones
```

## 🔒 Seguridad

- **API Keys:** Usar variables de entorno, nunca hardcodear
- **Webhooks:** Considerar validación de tokens
- **Rate Limiting:** Evolution API maneja limits automáticamente
- **HTTPS:** Usar certificados válidos en producción

## 📈 Próximas Mejoras

- [ ] Soporte para documentos PDF por WhatsApp
- [ ] Configuración de respuestas personalizadas
- [ ] Dashboard para monitoreo de mensajes
- [ ] Integración con múltiples instancias
- [ ] Autenticación de usuarios por WhatsApp

---

## 🆘 Soporte

Para problemas específicos:
1. Revisar logs del sistema
2. Verificar configuración de Evolution API
3. Comprobar conectividad de red
4. Validar variables de entorno

**¡La integración está lista para usar!** 🚀 