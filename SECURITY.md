# Política de Seguridad

## Versiones Soportadas

Actualmente damos soporte de seguridad a las siguientes versiones:

| Versión | Soportada          |
| ------- | ------------------ |
| main    | :white_check_mark: |
| < 1.0   | :x:                |

## Reportar una Vulnerabilidad

La seguridad de InvoiceFlow es una prioridad. Si descubres una vulnerabilidad de seguridad, por favor ayúdanos a mantener el proyecto seguro siguiendo estos pasos:

### Proceso de Reporte

1. **NO** crees un issue público en GitHub
2. Envía un correo electrónico a los maintainers del proyecto con:
   - Descripción detallada de la vulnerabilidad
   - Pasos para reproducirla
   - Impacto potencial
   - Sugerencias de mitigación (si las tienes)

3. Recibirás una respuesta inicial en 48 horas
4. Te mantendremos informado del progreso de la corrección
5. Coordinaremos contigo la divulgación pública una vez corregida

### Qué Esperar

- **Reconocimiento**: Confirmaremos la recepción de tu reporte en 48 horas
- **Validación**: Evaluaremos la vulnerabilidad en 5-7 días laborables
- **Corrección**: Trabajaremos en una solución según la severidad:
  - Crítica: < 7 días
  - Alta: < 14 días
  - Media: < 30 días
  - Baja: < 60 días
- **Divulgación**: Coordinaremos contigo la divulgación pública

### Política de Divulgación Responsable

Te pedimos que:

- Nos des tiempo razonable para corregir la vulnerabilidad antes de divulgarla públicamente
- Evites explotar la vulnerabilidad más allá de lo necesario para demostrarla
- No accedas, modifiques o elimines datos de otros usuarios
- Mantengas la confidencialidad hasta que se publique la corrección

### Reconocimiento

Reconoceremos públicamente tu contribución en:

- El changelog del proyecto
- Los release notes de la versión con la corrección
- Una sección de agradecimientos (a menos que prefieras permanecer anónimo)

## Mejores Prácticas de Seguridad

### Para Usuarios

1. **Variables de Entorno**
   - NUNCA compartas tu `.env` o expongas tus credenciales
   - Usa contraseñas fuertes y únicas
   - Rota regularmente tus API keys

2. **Despliegue**
   - Usa HTTPS en producción
   - Mantén las dependencias actualizadas
   - Configura límites de rate limiting apropiados
   - Revisa los logs regularmente

3. **Configuración**
   - Cambia las credenciales por defecto inmediatamente
   - Usa diferentes credenciales para cada entorno
   - Configura backups regulares de la base de datos

### Para Desarrolladores

1. **Código**
   - Valida y sanitiza todas las entradas de usuario
   - Usa prepared statements para queries SQL
   - Implementa autenticación y autorización apropiadas
   - No expongas información sensible en logs o mensajes de error

2. **Dependencias**
   - Mantén las dependencias actualizadas
   - Revisa regularmente vulnerabilidades conocidas
   - Usa herramientas como `safety` para auditar dependencias:
     ```bash
     pip install safety
     safety check
     ```

3. **Tests**
   - Incluye tests de seguridad en tu suite
   - Verifica la validación de entradas
   - Prueba la autenticación y autorización

## Alcance de Seguridad

### En Alcance

- Inyección SQL
- Cross-Site Scripting (XSS)
- Cross-Site Request Forgery (CSRF)
- Autenticación y autorización
- Exposición de información sensible
- Configuración insegura
- Vulnerabilidades en dependencias

### Fuera de Alcance

- Ingeniería social
- Ataques de fuerza bruta de credenciales
- Denial of Service (DoS)
- Vulnerabilidades en servicios de terceros (OpenAI, Evolution API, etc.)
- Problemas ya reportados o conocidos

## Contacto

Para reportes de seguridad, contacta a los maintainers del proyecto a través de los canales oficiales del repositorio.

## Actualizaciones de Seguridad

Las actualizaciones de seguridad se publicarán en:

- GitHub Security Advisories
- Release notes del proyecto
- Changelog del proyecto

Recomendamos suscribirse a las notificaciones del repositorio para estar al tanto de actualizaciones de seguridad.

---

Gracias por ayudarnos a mantener InvoiceFlow seguro.
