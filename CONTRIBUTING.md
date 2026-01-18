# Contribuir a InvoiceFlow Enterprise

¡Gracias por tu interés en contribuir a InvoiceFlow! Este documento te guiará en el proceso de contribución.

## Código de Conducta

Al participar en este proyecto, aceptas cumplir con nuestro [Código de Conducta](CODE_OF_CONDUCT.md).

## Cómo Contribuir

### Reportar Bugs

Si encuentras un bug, por favor abre un issue con:

- Descripción clara del problema
- Pasos para reproducirlo
- Comportamiento esperado vs. actual
- Versión del proyecto y entorno (OS, Python, etc.)
- Screenshots si aplica

### Sugerir Mejoras

Para sugerir nuevas características:

1. Revisa los issues existentes para evitar duplicados
2. Crea un issue describiendo:
   - El problema que resuelve
   - La solución propuesta
   - Alternativas consideradas

### Pull Requests

#### Proceso

1. **Fork** el repositorio
2. **Crea una rama** desde `main`:
   ```bash
   git checkout -b feature/mi-nueva-caracteristica
   ```
3. **Realiza tus cambios** siguiendo las guías de estilo
4. **Escribe tests** para tu código
5. **Asegúrate** de que todos los tests pasen
6. **Commit** tus cambios con mensajes descriptivos:
   ```bash
   git commit -m "feat: añadir validación de RNC para empresas"
   ```
7. **Push** a tu fork:
   ```bash
   git push origin feature/mi-nueva-caracteristica
   ```
8. **Abre un Pull Request** con:
   - Descripción clara de los cambios
   - Referencia a issues relacionados
   - Screenshots si afecta la UI

#### Guías de Estilo

**Python**
- Sigue PEP 8
- Usa type hints cuando sea posible
- Docstrings para funciones públicas
- Máximo 100 caracteres por línea

**Commits**
- Usa conventional commits:
  - `feat:` nueva característica
  - `fix:` corrección de bug
  - `docs:` documentación
  - `refactor:` refactorización
  - `test:` añadir tests
  - `chore:` tareas de mantenimiento

**Tests**
- Escribe tests para nuevas funcionalidades
- Mantén cobertura > 70%
- Ejecuta tests localmente antes de hacer PR:
  ```bash
  pytest tests/
  ```

### Configurar Entorno de Desarrollo

```bash
# Clonar el repositorio
git clone https://github.com/tu-usuario/invoiceflow.git
cd invoiceflow

# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Copiar archivo de configuración
cp .env.example .env

# Editar .env con tus credenciales
nano .env

# Inicializar base de datos
python check_db.py

# Ejecutar tests
pytest tests/

# Iniciar servidor
python main.py
```

## Áreas de Contribución

### Alta Prioridad

- Mejorar cobertura de tests
- Documentación y ejemplos
- Optimización de rendimiento
- Internacionalización (i18n)

### Características Deseadas

- Soporte para más formatos de exportación
- Integración con más plataformas de contabilidad
- Dashboard mejorado con más visualizaciones
- API REST completa con OpenAPI docs
- Autenticación OAuth2
- Multi-tenancy avanzado

### Bugs Conocidos

Revisa la sección de [Issues](https://github.com/tu-usuario/invoiceflow/issues) para bugs conocidos.

## Revisión de Código

Los maintainers revisarán tu PR y podrán:

- Aprobar y mergear
- Solicitar cambios
- Hacer comentarios o preguntas

Por favor sé paciente y receptivo al feedback.

## Licencia

Al contribuir, aceptas que tus contribuciones se licencien bajo la misma licencia MIT del proyecto.

## Preguntas

Si tienes preguntas, puedes:

- Abrir un issue con la etiqueta "question"
- Contactar a los maintainers

## Reconocimientos

Todos los contribuidores serán reconocidos en el README del proyecto.

¡Gracias por contribuir!
