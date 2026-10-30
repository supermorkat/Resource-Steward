# Resource Steward — Guía de uso

**Idiomas:** [English](README.md) · [العربية](README.ar.md) · [Français](README.fr.md) · Español

Resource Steward es una utilidad ligera de Python para vigilar la presión de recursos en sesiones Linux similares a Arena, comprobar las condiciones antes de tareas costosas, supervisar los comandos que inicia y organizar la revisión de archivos grandes.

> **Alcance y seguridad:** es un proyecto independiente y no oficial, no una función integrada de Arena o Google Drive. La herramienta no sube ni elimina archivos automáticamente. Revise los comandos y permisos antes de ejecutarlos.

## Funciones principales

- Lee la información de memoria cgroup v2 visible, la memoria disponible del sistema y el espacio libre en disco.
- Estima el número y el tamaño de los archivos que podrían incluirse en una instantánea de sesión.
- Realiza comprobaciones previas y avisa cuando hay presión sobre los recursos o cuando la situación se recupera.
- Puede supervisar un proceso hijo y detener su grupo de procesos si se alcanzan los límites configurados o falla la monitorización.
- Mantiene una cola local SQLite para revisar archivos grandes.
- Puede verificar el tamaño y la suma MD5 de un archivo que ya existe en Google Drive mediante un token de lectura configurado.
- Incluye un ejemplo de puntos de control y reanudación, siempre que la propia aplicación guarde y restaure su progreso.

## Requisitos

- Linux con `/proc` y cgroup v2.
- Python 3.10 o posterior.
- Una shell compatible con POSIX y Git.
- La utilidad principal no necesita paquetes externos de Python.

El proyecto está orientado a entornos Linux similares a Arena; no está documentado como herramienta general para Android/Termux, macOS o Windows.

## Primeros pasos

```bash
git clone https://github.com/supermorkat/Resource-Steward.git
cd Resource-Steward
python3 --version
python3 -m unittest discover -s resource-steward/tests -v
```

Consulte la documentación del repositorio y la ayuda de la línea de comandos para conocer la sintaxis exacta de la versión que utiliza. La monitorización no garantiza que una tarea pueda detenerse o reanudarse de forma segura.

## Precauciones importantes

- Las mediciones dependen de la información que expone el entorno y son estimaciones, no garantías.
- Un comando supervisado puede terminarse al alcanzar los umbrales configurados o si las mediciones dejan de estar disponibles.
- Los puntos de control deben implementarse en la propia aplicación.
- La verificación de Google Drive revisa un archivo ya subido; no realiza la subida.
- No incluya tokens de acceso en el código ni en el repositorio. Use el mecanismo de variables de entorno documentado.
- Revise los elementos de la cola de archivos grandes antes de actuar.

## Pruebas y desarrollo

Desde la raíz del repositorio:

```bash
python3 -m unittest discover -s resource-steward/tests -v
```

Actualice las pruebas si modifica los umbrales de recursos, la supervisión de procesos, el almacenamiento o la verificación de Drive.

## Licencia

La documentación no indica una licencia en el momento de preparar esta guía. No dé por hecho que se permite reutilizar, modificar o redistribuir el proyecto hasta que se añada una licencia explícita.

[Repositorio en GitHub](https://github.com/supermorkat/Resource-Steward)
