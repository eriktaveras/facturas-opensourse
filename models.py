from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from datetime import datetime
import os
import json
from dotenv import load_dotenv
import logging

# Configurar logging
logger = logging.getLogger(__name__)

load_dotenv()

# Detectar si estamos en Heroku (tiene PORT definido y DYNO o DATABASE_URL de postgres)
IS_HEROKU = os.getenv("DYNO") is not None or (os.getenv("DATABASE_URL") and os.getenv("DATABASE_URL").startswith("postgres"))

if IS_HEROKU:
    # En Heroku, usar la DATABASE_URL proporcionada por el addon PostgreSQL
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL no está configurada en Heroku")
    
    # Heroku usa postgres:// pero SQLAlchemy 1.4+ requiere postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    logger.info(f"🐘 Configurando PostgreSQL para Heroku: {DATABASE_URL[:50]}...")
else:
    # Desarrollo local - usar SQLite
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./invoices.db")
    logger.info("📄 Usando SQLite para desarrollo local")

# Configurar engine según el entorno
try:
    if DATABASE_URL.startswith("sqlite"):
        logger.info("📄 Configurando SQLite")
        engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    else:
        # PostgreSQL en Heroku
        logger.info("🐘 Configurando PostgreSQL para producción")
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,  # Verificar conexiones antes de usar
            pool_recycle=300,    # Renovar conexiones cada 5 min
            echo=False           # No mostrar SQL queries en logs
        )
        
    # Probar la conexión
    with engine.connect() as conn:
        logger.info("✅ Conexión a base de datos establecida correctamente")
        
except Exception as e:
    logger.error(f"❌ Error configurando base de datos: {e}")
    # Fallar rápido si no hay conexión a DB
    raise

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    tax_id = Column(String)
    plan = Column(String, default="Free Plan")
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    file_path = Column(String)
    file_type = Column(String)  # 'image' or 'pdf'
    
    # Datos extraídos de la factura
    vendor_name = Column(String)
    invoice_number = Column(String)
    invoice_date = Column(DateTime)
    total_amount = Column(Float)
    tax_amount = Column(Float)
    currency = Column(String, default="USD")
    
    # Clasificación
    transaction_type = Column(String)  # 'income' or 'expense'
    category = Column(String)
    description = Column(Text)
    
    # Datos de OpenAI
    raw_extracted_data = Column(Text)  # JSON string con datos completos de OpenAI
    confidence_score = Column(Float)
    audit_flags = Column(Text) # JSON string con alertas de auditoría (ej: ["high_price", "unknown_tax"])
    
    # Costos y métricas OpenAI
    openai_tokens_used = Column(Integer, default=0)
    openai_cost_usd = Column(Float, default=0.0)
    openai_model_used = Column(String)
    openai_processing_time = Column(Float)  # segundos
    
    # Datos fiscales del proveedor (nuevos campos)
    vendor_country = Column(String(3))  # ISO 3166-1 alpha-3 (USA, MEX, DOM, etc.)
    vendor_tax_id = Column(String)  # NIT/RNC/RFC/EIN/VAT
    vendor_fiscal_address = Column(Text)  # Dirección fiscal completa
    line_items_data = Column(Text)  # JSON: [{"description", "quantity", "unit_price", "subtotal"}]
    country_detection_method = Column(String)  # "ai_extracted", "currency_fallback", "tax_id_pattern"
    country_confidence = Column(Float)  # 0.0 - 1.0
    goods_services_type = Column(String)  # DGII 606: Tipo de Bienes y Servicios Comprados

    # Metadatos
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    processed = Column(Boolean, default=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    
    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "file_type": self.file_type,
            "vendor_name": self.vendor_name,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date.isoformat() if self.invoice_date else None,
            "total_amount": self.total_amount,
            "tax_amount": self.tax_amount,
            "currency": self.currency,
            "transaction_type": self.transaction_type,
            "category": self.category,
            "description": self.description,
            "confidence_score": self.confidence_score,
            "audit_flags": self.audit_flags,
            "openai_tokens_used": self.openai_tokens_used,
            "openai_cost_usd": self.openai_cost_usd,
            "openai_model_used": self.openai_model_used,
            "openai_processing_time": self.openai_processing_time,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "processed": self.processed,
            # Nuevos campos fiscales
            "vendor_country": self.vendor_country,
            "vendor_tax_id": self.vendor_tax_id,
            "vendor_fiscal_address": self.vendor_fiscal_address,
            "line_items": json.loads(self.line_items_data) if self.line_items_data else [],
            "country_detection_method": self.country_detection_method,
            "country_confidence": self.country_confidence,
            "organization_id": self.organization_id,
            "goods_services_type": self.goods_services_type
        }

class Setting(Base):
    __tablename__ = "settings"
    
    key = Column(String, primary_key=True, index=True)
    value = Column(String)
    type = Column(String)  # 'string', 'int', 'float', 'boolean', 'json'
    category = Column(String) # 'general', 'openai', 'whatsapp'
    description = Column(String)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserSetting(Base):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_settings_user_key"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    key = Column(String, index=True)
    value = Column(String)
    type = Column(String)  # 'string', 'int', 'float', 'boolean', 'json'
    category = Column(String) # 'general', 'openai', 'whatsapp'
    description = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, index=True)  # 'info', 'success', 'warning', 'error'
    title = Column(String)
    message = Column(String)
    data = Column(Text, nullable=True) # JSON string with extra data (e.g. invoice_id)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    
    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "data": self.data,
            "read": self.read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "time_ago": self.time_ago()
        }
        
    def time_ago(self):
        now = datetime.utcnow()
        diff = now - self.created_at
        
        if diff.days > 0:
            return f"hace {diff.days}d"
        elif diff.seconds > 3600:
            return f"hace {diff.seconds // 3600}h"
        elif diff.seconds > 60:
            return f"hace {diff.seconds // 60}m"
        else:
            return "ahora"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)

class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    description = Column(String)
    events = Column(Text) # JSON list of events: ["invoice.processed", "audit.alert"]
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    
    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "description": self.description,
            "events": json.loads(self.events) if self.events else [],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

def init_default_settings(db_session, org_id: int):
    """Inicializar configuraciones por defecto si no existen"""
    defaults = [
        # OpenAI / Costos
        {"key": "openai_api_key", "value": os.getenv("OPENAI_API_KEY", ""), "type": "password", "category": "openai", "description": "API Key de OpenAI (sk-...)"},
        {"key": "openai_model", "value": "gpt-4o", "type": "string", "category": "openai", "description": "Modelo de IA utilizado para procesamiento"},
        {"key": "openai_daily_limit", "value": "10.0", "type": "float", "category": "openai", "description": "Límite de costo diario en USD"},
        {"key": "openai_max_tokens", "value": "4000", "type": "int", "category": "openai", "description": "Máximo de tokens por petición"},
        
        # General / Empresa
        {"key": "company_name", "value": "Mi Empresa S.A.", "type": "string", "category": "general", "description": "Nombre de la empresa"},
        {"key": "company_tax_id", "value": "", "type": "string", "category": "general", "description": "ID Fiscal / RNC / CIF"},
        {"key": "company_address", "value": "", "type": "string", "category": "general", "description": "Dirección fiscal"},
        {"key": "default_currency", "value": "USD", "type": "string", "category": "general", "description": "Moneda predeterminada"},
        {"key": "company_plan", "value": "Free Plan", "type": "string", "category": "general", "description": "Plan de la empresa"},
        
        # WhatsApp / Seguridad
        {"key": "authorized_whatsapp_number", "value": os.getenv("AUTHORIZED_WHATSAPP_NUMBER", "15555550100"), "type": "string", "category": "whatsapp", "description": "Número autorizado (WhatsApp)"},
        {"key": "whatsapp_auto_reply", "value": "true", "type": "boolean", "category": "whatsapp", "description": "Respuesta automática WhatsApp"},
        {"key": "evolution_url", "value": "https://your-evolution-api.example.com", "type": "string", "category": "whatsapp", "description": "URL de Evolution API"},
        {"key": "evolution_apikey", "value": "YOUR_EVOLUTION_API_KEY", "type": "password", "category": "whatsapp", "description": "API Key de Evolution"},
        {"key": "evolution_instance", "value": "Taveras Solutions LLC", "type": "string", "category": "whatsapp", "description": "Nombre de la instancia"},
        {"key": "security_max_upload_size_mb", "value": "10", "type": "int", "category": "security", "description": "Tamaño máx. de archivo (MB)"},
        
        # Correo / Notificaciones
        {"key": "smtp_host", "value": "", "type": "string", "category": "email", "description": "Servidor SMTP"},
        {"key": "smtp_port", "value": "587", "type": "int", "category": "email", "description": "Puerto SMTP"},
        {"key": "smtp_user", "value": "", "type": "string", "category": "email", "description": "Usuario SMTP"},
        {"key": "smtp_password", "value": "", "type": "password", "category": "email", "description": "Contraseña SMTP"},
        {"key": "notification_email", "value": "", "type": "string", "category": "email", "description": "Email para alertas"},
        
        # Almacenamiento
        {"key": "storage_retention_days", "value": "365", "type": "int", "category": "storage", "description": "Días de retención de archivos"}
    ]
    
    try:
        for setting in defaults:
            exists = db_session.query(Setting).filter(Setting.key == setting["key"]).first()
            if not exists:
                new_setting = Setting(
                    key=setting["key"],
                    value=setting["value"],
                    type=setting["type"],
                    category=setting["category"],
                    description=setting["description"],
                    organization_id=org_id
                )
                db_session.add(new_setting)
        db_session.commit()
        logger.info("⚙️ Configuraciones por defecto verificadas")
    except Exception as e:
        logger.error(f"❌ Error inicializando settings: {e}")
        db_session.rollback()

def migrate_invoices_table(engine):
    """
    Migración manual para agregar columnas faltantes a la tabla invoices (SQLite/Postgres)
    """
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        if "invoices" not in inspector.get_table_names():
            return

        columns = [c["name"] for c in inspector.get_columns("invoices")]

        # Columnas nuevas a verificar (ajustado para PostgreSQL)
        if IS_HEROKU:
            # PostgreSQL
            new_columns = {
                "openai_tokens_used": "INTEGER DEFAULT 0",
                "openai_cost_usd": "DOUBLE PRECISION DEFAULT 0.0",
                "openai_model_used": "VARCHAR(100)",
                "openai_processing_time": "DOUBLE PRECISION",
                "audit_flags": "TEXT",
                "vendor_country": "VARCHAR(3)",
                "vendor_tax_id": "VARCHAR(255)",
                "vendor_fiscal_address": "TEXT",
                "line_items_data": "TEXT",
                "country_detection_method": "VARCHAR(100)",
                "country_confidence": "DOUBLE PRECISION",
                "goods_services_type": "VARCHAR(10)",
                "organization_id": "INTEGER"
            }
        else:
            # SQLite
            new_columns = {
                "openai_tokens_used": "INTEGER DEFAULT 0",
                "openai_cost_usd": "FLOAT DEFAULT 0.0",
                "openai_model_used": "VARCHAR",
                "openai_processing_time": "FLOAT",
                "audit_flags": "TEXT",
                "vendor_country": "VARCHAR(3)",
                "vendor_tax_id": "VARCHAR",
                "vendor_fiscal_address": "TEXT",
                "line_items_data": "TEXT",
                "country_detection_method": "VARCHAR",
                "country_confidence": "FLOAT",
                "goods_services_type": "VARCHAR",
                "organization_id": "INTEGER"
            }

        with engine.begin() as conn:  # Usar begin() para autocommit
            for col_name, col_type in new_columns.items():
                if col_name not in columns:
                    logger.info(f"🔄 Migrando BD: Agregando columna '{col_name}' a 'invoices'...")
                    conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {col_name} {col_type}"))
                    logger.info(f"✅ Columna '{col_name}' agregada exitosamente")

    except Exception as e:
        logger.error(f"❌ Error en migración manual: {e}")
        import traceback
        logger.error(traceback.format_exc())

def migrate_multitenant_tables(engine):
    """
    Migración manual para agregar columnas faltantes a tablas multi-tenant
    """
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        table_names = inspector.get_table_names()

        if IS_HEROKU:
            org_col_type = "INTEGER"
        else:
            org_col_type = "INTEGER"

        tables_to_update = {
            "users": "organization_id",
            "notifications": "organization_id",
            "webhook_endpoints": "organization_id",
            "settings": "organization_id"
        }

        with engine.begin() as conn:
            for table_name, column_name in tables_to_update.items():
                if table_name not in table_names:
                    continue
                columns = [c["name"] for c in inspector.get_columns(table_name)]
                if column_name not in columns:
                    logger.info(f"🔄 Migrando BD: Agregando columna '{column_name}' a '{table_name}'...")
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {org_col_type}"))
                    logger.info(f"✅ Columna '{column_name}' agregada exitosamente en '{table_name}'")

    except Exception as e:
        logger.error(f"❌ Error en migración multi-tenant: {e}")
        import traceback
        logger.error(traceback.format_exc())

def init_database():
    """Inicializar la base de datos de forma segura"""
    try:
        # Usar checkfirst=True para evitar errores si las tablas ya existen
        Base.metadata.create_all(bind=engine, checkfirst=True)
        
        # Ejecutar migración manual si es necesario
        migrate_invoices_table(engine)
        migrate_multitenant_tables(engine)
        
        logger.info("✅ Tablas de base de datos inicializadas correctamente")
        
        # Inicializar datos semilla
        db = SessionLocal()
        # Crear organización por defecto si no existe
        org = db.query(Organization).first()
        if not org:
            default_name = "Mi Empresa S.A."
            org = Organization(name=default_name, tax_id="", plan="Free Plan")
            db.add(org)
            db.commit()
            db.refresh(org)

        # Asignar org por defecto a registros sin organización
        db.query(User).filter(User.organization_id.is_(None)).update({"organization_id": org.id})
        db.query(Invoice).filter(Invoice.organization_id.is_(None)).update({"organization_id": org.id})
        db.query(Notification).filter(Notification.organization_id.is_(None)).update({"organization_id": org.id})
        db.query(WebhookEndpoint).filter(WebhookEndpoint.organization_id.is_(None)).update({"organization_id": org.id})
        db.query(Setting).filter(Setting.organization_id.is_(None)).update({"organization_id": org.id})
        db.commit()

        # Inicializar settings por organización
        init_default_settings(db, org.id)
        db.close()
        
    except Exception as e:
        logger.error(f"❌ Error inicializando tablas: {e}")
        # No hacer raise aquí - las tablas pueden ya existir
        pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close() 
