# DeltaChat — Tablón Web Privado Estilo 4chan

**DeltaChat** es una aplicación web de chat privado en tiempo real con estética **Tomorrow Dark (estilo 4chan / Imageboard)**, diseñada para funcionar en **PC, Tablets y Celulares** y compatible con **Chrome, Firefox, Edge, Brave y Tor Browser** (sin dependencias externas ni CDNs).

---

## Características principales

* **Estética 4chan / Imageboard:** Fondo oscuro *Tomorrow Dark*, soporte para **greentext** (`>texto en verde`), enlaces de cita `>>No.`, metadatos de archivos (`File: nombre.webp (KB, resolución)`), y logo personalizado.
* **Tablones y Mensajes Directos:** Tablones `/gen/`, `/memes/`, `/v/`, `/mu/` más chats privados 1 a 1 (DMs).
* **Control de Administrador (`dabo777`):**
  * Solo tú conoces tu usuario y contraseña (`dabo777`).
  * Todos los usuarios (incluido tú) pueden cambiar su **Alias público**; tus amigos solo ven tu alias, mientras que tú como Admin puedes ver el **alias + usuario real + IP** de todos.
  * **Baneos y Suspensiones:** Baneo permanente por IP (**Permaban**), suspensión temporal por tiempo (10m, 1h, 24h, 7d o personalizado) y panel exclusivo para **quitar baneos**.
* **Imágenes optimizadas y Modo HD efímero (10 minutos):**
  * Fotos estándar: límite 5 MB, compresión automática a ~300 KB en formato WebP sin metadatos EXIF.
  * Fotos HD: disponibles durante **10 minutos** con botón de descarga directa y visor **Lightbox con zoom**; al cumplirse el plazo el archivo se borra del servidor y aparece el aviso de finalización.
* **Respaldo mensual automático a GitHub:**
  * Cada mes se genera automáticamente el archivo `backup_MM-DD-YYYY.txt` con todos los mensajes (usuario, fecha y texto; sin adjuntos), se sube a tu repositorio de GitHub y se reinicia el chat para empezar el nuevo mes limpio.

---

## Ejecución en Local

1. Activa el entorno virtual e instala dependencias (si aún no lo están):
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. Inicia el servidor:
   ```powershell
   .\.venv\Scripts\python.exe main.py
   ```
3. Abre tu navegador en `http://localhost:8000`.

---

## Despliegue Gratis 24/7 en Render.com

1. Sube este proyecto a un repositorio en tu cuenta de **GitHub**.
2. Crea una cuenta gratuita en [Render.com](https://render.com) y selecciona **New + -> Web Service** (o **Blueprint** usando `render.yaml`).
3. Conecta tu repositorio.
4. En **Environment Variables** configura:
   * `ADMIN_USERNAME`: `dabo777`
   * `ADMIN_PASSWORD`: `52952416` (o la contraseña que desees usar en producción)
   * `GITHUB_TOKEN`: Tu *Personal Access Token* de GitHub (con permiso `repo` para que guarde los respaldos mensuales).
   * `GITHUB_REPO`: Tu repositorio destino, por ejemplo `tu-usuario/deltachat-backups`.
5. ¡Listo! Render te dará un enlace público `https://deltachat-xxxx.onrender.com` para compartir con tus amigos.

> **Nota sobre almacenamiento en Render Free:** En el plan gratuito de Render el disco local es efímero tras reinicios del contenedor. Para conservar la base de datos permanentemente entre redespliegues puedes añadir un **Persistent Disk** en Render apuntando a `/app/data` (configurando `DELTACHAT_DB=/app/data/deltachat.db` y `DELTACHAT_UPLOADS=/app/data/uploads`). El respaldo mensual automático a GitHub asegura además que tus historiales queden guardados en la nube.
