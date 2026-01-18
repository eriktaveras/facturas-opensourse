#!/usr/bin/env python3
"""
🧪 Script de prueba para el sistema directo con Evolution API
Verifica que el endpoint getBase64FromMediaMessage funcione correctamente
Ahora siempre obtiene la imagen original sin thumbnails ni fallbacks
"""

import requests
import json
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

def test_evolution_api_connection():
    """Prueba la conexión básica con Evolution API"""
    print("🔍 Verificando conexión con Evolution API...")
    
    evolution_url = os.getenv("EVOLUTION_API_URL", "https://your-evolution-api.example.com")
    api_key = os.getenv("EVOLUTION_API_KEY", "YOUR_EVOLUTION_API_KEY")
    instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "your_instance")
    
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Verificar estado de la instancia
        response = requests.get(
            f"{evolution_url}/instance/connectionState/{instance_name}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Conexión exitosa con Evolution API")
            print(f"📱 Estado de instancia: {data}")
            return True
        else:
            print(f"❌ Error conectando: {response.status_code}")
            print(f"📄 Respuesta: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        return False

def test_local_endpoint():
    """Prueba el endpoint local de test"""
    print("\n🧪 Probando endpoint local de test...")
    
    # URL del endpoint local
    local_url = "http://localhost:8000/evolution/test-get-base64"
    
    # ID de mensaje de prueba (usar uno real para prueba completa)
    test_message_id = "TEST_MESSAGE_ID_123"
    
    payload = {
        "message_id": test_message_id,
        "instance_name": os.getenv("EVOLUTION_INSTANCE_NAME", "your_instance")
    }
    
    try:
        response = requests.post(
            local_url,
            json=payload,
            timeout=30
        )
        
        print(f"📊 Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Endpoint local funcionando")
            print(f"📄 Respuesta: {json.dumps(data, indent=2)}")
            return True
        else:
            print(f"⚠️ Endpoint respondió con error: {response.status_code}")
            print(f"📄 Respuesta: {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ No se pudo conectar al servidor local")
        print("💡 Asegúrate de que el servidor esté ejecutándose con: python3 main.py")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_webhook_simulation():
    """Simula un webhook para probar el sistema directo a Evolution API"""
    print("\n📱 Simulando webhook con imagen...")
    
    # Webhook simulado que activará el flujo directo a Evolution API
    webhook_payload = {
        "event": "messages.upsert",
        "sender": "5491234567890@s.whatsapp.net",
        "data": {
            "key": {
                "id": "TEST_MESSAGE_EVOLUTION_DIRECT"
            },
            "pushName": "Usuario Test",
            "message": {
                "imageMessage": {
                    "url": "https://mmg.whatsapp.net/v/t62.7118-24/example.enc"
                }
            }
        }
    }
    
    webhook_url = "http://localhost:8000/evolution/webhook"
    
    try:
        response = requests.post(
            webhook_url,
            json=webhook_payload,
            timeout=30
        )
        
        print(f"📊 Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Webhook procesado correctamente")
            print(f"📄 Resultado: {json.dumps(data, indent=2)}")
            
            # Verificar si se procesó con Evolution API
            if "results" in data and data["results"]:
                result = data["results"][0]
                if result.get("status") == "success" and "Evolution API" in str(result):
                    print("🎯 ¡Procesamiento directo con Evolution API exitoso!")
                elif result.get("status") == "error":
                    print(f"⚠️ Error esperado (API test): {result.get('error', 'Unknown')}")
                else:
                    print("🔄 Webhook procesado correctamente")
            
            return True
        else:
            print(f"❌ Error en webhook: {response.status_code}")
            print(f"📄 Respuesta: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error simulando webhook: {e}")
        return False

def main():
    """Ejecuta todas las pruebas"""
    print("🚀 Iniciando pruebas del sistema directo Evolution API\n")
    
    tests = [
        ("Conexión Evolution API", test_evolution_api_connection),
        ("Endpoint local test", test_local_endpoint),
        ("Simulación webhook", test_webhook_simulation)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"📋 Ejecutando: {test_name}")
        print(f"{'='*50}")
        
        success = test_func()
        results.append((test_name, success))
    
    # Resumen de resultados
    print(f"\n{'='*50}")
    print("📊 RESUMEN DE PRUEBAS")
    print(f"{'='*50}")
    
    for test_name, success in results:
        status = "✅ PASÓ" if success else "❌ FALLÓ"
        print(f"{status} {test_name}")
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    print(f"\n🎯 Resultado final: {passed}/{total} pruebas exitosas")
    
    if passed == total:
        print("🎉 ¡Todas las pruebas pasaron! El sistema está funcionando correctamente.")
    else:
        print("⚠️ Algunas pruebas fallaron. Revisa la configuración y conectividad.")
    
    print("\n💡 Para prueba completa con mensaje real de WhatsApp:")
    print("   1. Envía una imagen por WhatsApp al bot")
    print("   2. Copia el message.key.id de los logs")
    print("   3. Ejecuta: curl -X POST 'http://localhost:8000/evolution/test-get-base64' -H 'Content-Type: application/json' -d '{\"message_id\": \"TU_MESSAGE_ID\"}'")

if __name__ == "__main__":
    main() 