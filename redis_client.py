import os
import redis
import json
from typing import Optional, Any
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

# Configuración de Redis
REDIS_URL = os.getenv("REDIS_URL")

# Cliente Redis global
redis_client: Optional[redis.Redis] = None

def get_redis_client() -> Optional[redis.Redis]:
    """
    Obtiene el cliente Redis (singleton pattern)
    """
    global redis_client

    if redis_client is None:
        if not REDIS_URL:
            logger.warning("⚠️ REDIS_URL no configurada, Redis deshabilitado")
            return None

        try:
            # Heroku Redis usa SSL, así que necesitamos ssl_cert_reqs=None
            redis_client = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                ssl_cert_reqs=None  # Necesario para Heroku Redis
            )

            # Test connection
            redis_client.ping()
            logger.info("✅ Redis conectado correctamente")

        except Exception as e:
            logger.error(f"❌ Error conectando a Redis: {e}")
            redis_client = None

    return redis_client


def cache_get(key: str) -> Optional[Any]:
    """
    Obtiene valor del caché Redis
    """
    try:
        r = get_redis_client()
        if not r:
            return None

        value = r.get(key)
        if value:
            try:
                return json.loads(value)
            except:
                return value
        return None

    except Exception as e:
        logger.error(f"Error en cache_get({key}): {e}")
        return None


def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    """
    Guarda valor en caché Redis con TTL (segundos)
    """
    try:
        r = get_redis_client()
        if not r:
            return False

        # Serializar a JSON si es dict/list
        if isinstance(value, (dict, list)):
            value = json.dumps(value)

        r.setex(key, ttl, value)
        return True

    except Exception as e:
        logger.error(f"Error en cache_set({key}): {e}")
        return False


def cache_delete(key: str) -> bool:
    """
    Elimina valor del caché
    """
    try:
        r = get_redis_client()
        if not r:
            return False

        r.delete(key)
        return True

    except Exception as e:
        logger.error(f"Error en cache_delete({key}): {e}")
        return False


def rate_limit(key: str, limit: int = 10, window: int = 60) -> bool:
    """
    Rate limiting usando Redis

    Args:
        key: Identificador único (ej: "webhook:15555550100")
        limit: Número máximo de requests permitidos
        window: Ventana de tiempo en segundos

    Returns:
        True si está dentro del límite, False si excede
    """
    try:
        r = get_redis_client()
        if not r:
            return True  # Si Redis no está disponible, permitir

        current = r.incr(key)

        # Si es el primer request, establecer expiración
        if current == 1:
            r.expire(key, window)

        # Verificar si excede el límite
        if current > limit:
            logger.warning(f"⚠️ Rate limit excedido para {key}: {current}/{limit}")
            return False

        return True

    except Exception as e:
        logger.error(f"Error en rate_limit({key}): {e}")
        return True  # En caso de error, permitir (fail open)


def is_duplicate_message(message_id: str, ttl: int = 86400) -> bool:
    """
    Verifica si un mensaje ya fue procesado (deduplicación)

    Args:
        message_id: ID único del mensaje
        ttl: Tiempo de retención en segundos (default: 24 horas)

    Returns:
        True si ya existe (es duplicado), False si es nuevo
    """
    try:
        r = get_redis_client()
        if not r:
            return False  # Si Redis no está disponible, asumir que es nuevo

        key = f"processed:msg:{message_id}"

        # Verificar si existe
        if r.exists(key):
            logger.warning(f"⚠️ Mensaje duplicado detectado: {message_id}")
            return True

        # Marcar como procesado
        r.setex(key, ttl, "1")
        return False

    except Exception as e:
        logger.error(f"Error en is_duplicate_message({message_id}): {e}")
        return False  # En caso de error, asumir que es nuevo


def invalidate_cache_pattern(pattern: str) -> int:
    """
    Invalida todas las claves que coincidan con un patrón

    Args:
        pattern: Patrón de Redis (ej: "stats:*", "settings:*")

    Returns:
        Número de claves eliminadas
    """
    try:
        r = get_redis_client()
        if not r:
            return 0

        # Encontrar todas las claves que coincidan
        keys = r.keys(pattern)

        if keys:
            deleted = r.delete(*keys)
            logger.info(f"🗑️ Invalidadas {deleted} claves de caché ({pattern})")
            return deleted

        return 0

    except Exception as e:
        logger.error(f"Error en invalidate_cache_pattern({pattern}): {e}")
        return 0


def get_cache_stats() -> dict:
    """
    Obtiene estadísticas de Redis
    """
    try:
        r = get_redis_client()
        if not r:
            return {"status": "disabled"}

        info = r.info("stats")

        return {
            "status": "connected",
            "total_commands": info.get("total_commands_processed", 0),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "hit_rate": round(
                info.get("keyspace_hits", 0) /
                max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1) * 100,
                2
            )
        }

    except Exception as e:
        logger.error(f"Error obteniendo stats de Redis: {e}")
        return {"status": "error", "error": str(e)}
