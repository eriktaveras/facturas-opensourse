#!/bin/bash

# 🐘 Script para configurar PostgreSQL en Heroku

echo "🚀 Configurando PostgreSQL en Heroku..."

# Reemplaza 'tu-nombre-app' con el nombre real de tu app
APP_NAME="tu-nombre-app-facturas"

echo "📱 App: $APP_NAME"

# 1. Agregar addon de PostgreSQL
echo "📦 Agregando addon PostgreSQL..."
heroku addons:create heroku-postgresql:essential-0 --app $APP_NAME

# 2. Verificar que se agregó
echo "🔍 Verificando addon PostgreSQL..."
heroku addons --app $APP_NAME

# 3. Ver la URL de la base de datos
echo "🔗 URL de la base de datos:"
heroku config:get DATABASE_URL --app $APP_NAME

# 4. Configurar variables de entorno
echo "⚙️ Configurando variables de entorno..."

# OpenAI API Key (DEBES CAMBIAR ESTO)
read -p "🤖 Ingresa tu OpenAI API Key: " OPENAI_KEY
heroku config:set OPENAI_API_KEY="$OPENAI_KEY" --app $APP_NAME

# Evolution API (WhatsApp)
heroku config:set EVOLUTION_API_URL=https://your-evolution-api.example.com --app $APP_NAME
heroku config:set EVOLUTION_API_KEY=YOUR_EVOLUTION_API_KEY --app $APP_NAME
heroku config:set EVOLUTION_INSTANCE_NAME=your_instance --app $APP_NAME

# 5. Ver todas las variables configuradas
echo "📋 Variables de entorno configuradas:"
heroku config --app $APP_NAME

# 6. Deploy con las nuevas configuraciones
echo "🚀 Haciendo deploy..."
git add .
git commit -m "Configure PostgreSQL and environment variables"
git push heroku main

# 7. Ver logs del deploy
echo "📊 Logs del deploy:"
heroku logs --tail --app $APP_NAME

echo "✅ ¡Configuración completada!"
echo "🌐 Abrir app: heroku open --app $APP_NAME" 