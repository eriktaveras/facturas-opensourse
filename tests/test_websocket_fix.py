#!/usr/bin/env python3
"""
Script de prueba para verificar las correcciones de WebSocket y estadísticas
"""
import asyncio
import json
import requests
import websockets
from datetime import datetime

async def test_websocket_connection():
    """Probar conexión WebSocket"""
    print("🔌 Probando conexión WebSocket...")
    
    try:
        # Determinar el protocolo correcto
        base_url = "localhost:8000"  # Cambiar por tu URL de producción
        ws_url = f"ws://{base_url}/ws"
        
        async with websockets.connect(ws_url) as websocket:
            print("✅ WebSocket conectado exitosamente")
            
            # Enviar ping
            await websocket.send("ping")
            print("📤 Ping enviado")
            
            # Esperar respuesta
            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            message = json.loads(response)
            print(f"📥 Respuesta recibida: {message}")
            
            # Esperar por algunos mensajes más
            print("⏳ Esperando notificaciones por 10 segundos...")
            try:
                for _ in range(3):
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    data = json.loads(message)
                    print(f"📨 Notificación: {data.get('type', 'unknown')} - {data.get('message', 'Sin mensaje')}")
            except asyncio.TimeoutError:
                print("⏰ Timeout esperando más mensajes")
            
            return True
            
    except Exception as e:
        print(f"❌ Error conectando WebSocket: {e}")
        return False

def test_statistics_endpoint():
    """Probar endpoint de estadísticas"""
    print("\n📊 Probando endpoint de estadísticas...")
    
    try:
        base_url = "http://localhost:8000"  # Cambiar por tu URL de producción
        response = requests.get(f"{base_url}/statistics", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Estadísticas obtenidas exitosamente")
            
            # Verificar estructura de datos
            required_fields = ['general', 'totals', 'categories', 'openai_costs']
            missing_fields = []
            
            for field in required_fields:
                if field not in data:
                    missing_fields.append(field)
            
            if missing_fields:
                print(f"⚠️ Campos faltantes en estadísticas: {missing_fields}")
            else:
                print("✅ Estructura de estadísticas correcta")
            
            # Mostrar resumen de datos
            general = data.get('general', {})
            totals = data.get('totals', {})
            
            print(f"📋 Resumen de estadísticas:")
            print(f"   📄 Total facturas: {general.get('total_invoices', 0)}")
            print(f"   ✅ Procesadas: {general.get('processed_invoices', 0)}")
            print(f"   ⏳ Pendientes: {general.get('pending_invoices', 0)}")
            print(f"   💰 Ingresos: ${totals.get('income', {}).get('amount', 0):,.2f}")
            print(f"   💸 Gastos: ${totals.get('expense', {}).get('amount', 0):,.2f}")
            print(f"   📈 Balance neto: ${totals.get('net', 0):,.2f}")
            
            categories = data.get('categories', [])
            print(f"   🏷️ Categorías: {len(categories)}")
            
            return True
        else:
            print(f"❌ Error en endpoint de estadísticas: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error probando estadísticas: {e}")
        return False

def test_websocket_status_endpoint():
    """Probar endpoint de estado WebSocket"""
    print("\n🔍 Probando endpoint de estado WebSocket...")
    
    try:
        base_url = "http://localhost:8000"  # Cambiar por tu URL de producción
        response = requests.get(f"{base_url}/websocket/status", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Estado WebSocket obtenido exitosamente")
            
            ws_status = data.get('websocket_status', {})
            print(f"📊 Estado WebSocket:")
            print(f"   🔗 Conexiones activas: {ws_status.get('active_connections', 0)}")
            print(f"   📬 Notificaciones enviadas: {ws_status.get('notifications_sent', 0)}")
            print(f"   🔄 Estado: {ws_status.get('status', 'unknown')}")
            
            return True
        else:
            print(f"❌ Error en endpoint de estado WebSocket: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error probando estado WebSocket: {e}")
        return False

def test_security_config():
    """Probar configuración de seguridad"""
    print("\n🔒 Probando configuración de seguridad...")
    
    try:
        base_url = "http://localhost:8000"  # Cambiar por tu URL de producción
        response = requests.get(f"{base_url}/evolution/security-config", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Configuración de seguridad obtenida")
            print(f"🔐 Número autorizado: {data.get('authorized_number', 'N/A')}")
            print(f"🛡️ Seguridad habilitada: {data.get('security_enabled', False)}")
            
            return True
        else:
            print(f"❌ Error en configuración de seguridad: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error probando seguridad: {e}")
        return False

async def main():
    """Ejecutar todas las pruebas"""
    print("🚀 Iniciando pruebas de correcciones WebSocket y estadísticas")
    print("=" * 60)
    
    results = []
    
    # Probar endpoints HTTP primero
    results.append(test_statistics_endpoint())
    results.append(test_websocket_status_endpoint()) 
    results.append(test_security_config())
    
    # Probar WebSocket al final
    results.append(await test_websocket_connection())
    
    # Resumen de resultados
    print("\n" + "=" * 60)
    print("📋 RESUMEN DE PRUEBAS:")
    
    passed = sum(results)
    total = len(results)
    
    print(f"✅ Pruebas exitosas: {passed}/{total}")
    
    if passed == total:
        print("🎉 ¡Todas las pruebas pasaron! El sistema está funcionando correctamente.")
    else:
        print(f"⚠️ {total - passed} prueba(s) fallaron. Revisar la configuración.")
    
    print("\n💡 Consejos:")
    print("   - Asegúrate de que el servidor esté ejecutándose")
    print("   - Verifica la URL base en el script")
    print("   - En producción, cambia 'localhost:8000' por tu dominio")
    print("   - WebSocket usa WSS en HTTPS automáticamente")

if __name__ == "__main__":
    print("📋 Script de prueba - Correcciones WebSocket y Estadísticas")
    print("🔧 Para usar este script:")
    print("   1. Asegúrate de que tu servidor esté ejecutándose")
    print("   2. Modifica las URLs base si es necesario")
    print("   3. Ejecuta: python test_websocket_fix.py")
    print("")
    
    # Ejecutar pruebas
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️ Pruebas interrumpidas por el usuario")
    except Exception as e:
        print(f"\n❌ Error ejecutando pruebas: {e}") 