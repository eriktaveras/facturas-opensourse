# 🚀 Configuración para Deploy en Heroku

## Archivos preparados para Heroku ✅

- `requirements.txt` - Dependencias de Python
- `Procfile` - Configuración de ejecución
- `runtime.txt` - Versión de Python
- Configuración de puerto dinámico
- Soporte para PostgreSQL

## Pasos para deploy en Heroku

### 1. Instalar Heroku CLI
```bash
# macOS
brew install heroku/brew/heroku

# Otros sistemas: https://devcenter.heroku.com/articles/heroku-cli
```

### 2. Login y crear app
```bash
heroku login
heroku create tu-nombre-app-facturas
```

### 3. Agregar addon de PostgreSQL
```bash
# Agregar PostgreSQL (Base de datos en la nube)
heroku addons:create heroku-postgresql:essential-0 --app tu-nombre-app-facturas

# Verificar que se agregó correctamente
heroku addons --app tu-nombre-app-facturas

# Ver la URL de conexión (automática)
heroku config:get DATABASE_URL --app tu-nombre-app-facturas
```

**💡 Nota**: El addon PostgreSQL agrega automáticamente la variable `DATABASE_URL` que la app usa para conectarse.

### 4. Configurar variables de entorno
```bash
# OpenAI API Key (REQUERIDO)
heroku config:set OPENAI_API_KEY=tu_openai_api_key_real --app tu-nombre-app-facturas

# Evolution API (para WhatsApp)
heroku config:set EVOLUTION_API_URL=https://your-evolution-api.example.com --app tu-nombre-app-facturas
heroku config:set EVOLUTION_API_KEY=YOUR_EVOLUTION_API_KEY --app tu-nombre-app-facturas
heroku config:set EVOLUTION_INSTANCE_NAME=your_instance --app tu-nombre-app-facturas
```

### 5. Deploy
```bash
git add .
git commit -m "Preparar para deploy en Heroku"
git push heroku main
```

### 6. Deploy con PostgreSQL
```bash
# Hacer commit de los cambios
git add .
git commit -m "Configure PostgreSQL support and auto table creation"

# Deploy a Heroku
git push heroku main
```

**✅ Las tablas se crean automáticamente** al arrancar la aplicación.

### 7. Ver logs
```bash
heroku logs --tail --app tu-nombre-app-facturas
```

### 8. Abrir app
```bash
heroku open --app tu-nombre-app-facturas
```

## URLs de la aplicación

Una vez deployado, tendrás estas URLs:

- **Dashboard**: `https://tu-nombre-app-facturas.herokuapp.com/`
- **API Docs**: `https://tu-nombre-app-facturas.herokuapp.com/docs`
- **Webhook WhatsApp**: `https://tu-nombre-app-facturas.herokuapp.com/evolution/webhook`

## Configurar Webhook en Evolution API

```bash
curl -X POST https://your-evolution-api.example.com/webhook/set/your_instance \
  -H "Content-Type: application/json" \
  -H "apikey: YOUR_EVOLUTION_API_KEY" \
  -d '{
    "url": "https://tu-nombre-app-facturas.herokuapp.com/evolution/webhook",
    "enabled": true,
    "events": ["messages.upsert"]
  }'
```

## Variables de entorno requeridas

| Variable | Descripción | Requerido |
|----------|-------------|-----------|
| `OPENAI_API_KEY` | Clave API de OpenAI para procesamiento de facturas | ✅ |
| `EVOLUTION_API_URL` | URL del servidor Evolution API | ✅ |
| `EVOLUTION_API_KEY` | Clave API de Evolution | ✅ |
| `EVOLUTION_INSTANCE_NAME` | Nombre de la instancia WhatsApp | ✅ |
| `DATABASE_URL` | URL de PostgreSQL (auto-configurada por Heroku) | ✅ |

## Consideraciones importantes

### 🗂️ **Archivos temporales**
- Los archivos subidos en Heroku son temporales
- Se eliminan cuando el dyno se reinicia
- Para producción, considera usar AWS S3 o similar

### 💰 **Costos**
- Plan básico de Heroku: Gratis hasta cierto uso
- PostgreSQL Essential: ~$5/mes
- Verificar precios actuales en Heroku

### 🔐 **Seguridad**
- Todas las API keys están configuradas como variables de entorno
- No se incluyen secretos en el código
- SSL/HTTPS habilitado automáticamente

### 📊 **Monitoreo**
```bash
# Ver status de la app
heroku ps --app tu-nombre-app-facturas

# Ver métricas
heroku logs --tail --app tu-nombre-app-facturas

# Escalar dynos si es necesario
heroku ps:scale web=1 --app tu-nombre-app-facturas
```

## Testing después del deploy

1. **Verificar dashboard**: Abrir la URL principal
2. **Test WhatsApp**: Enviar imagen por WhatsApp
3. **Verificar logs**: `heroku logs --tail`
4. **Test API**: Usar `/docs` para probar endpoints

---

🎉 **¡Tu aplicación de gestión de facturas está lista para producción!** 