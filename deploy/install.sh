#!/usr/bin/env bash
#
# Instala el receptor de webhooks en un servidor Linux con systemd y Docker.
# Idempotente: se puede volver a ejecutar para actualizar el codigo.
#
#   sudo ./deploy/install.sh
#
set -euo pipefail

APP_DIR=/opt/cd-receiver
PROXY_DIR=/srv/infra/socket-proxy
CONF_DIR=/etc/cd-receiver
STATE_DIR=/var/lib/cd-receiver
LOG_DIR=/var/log/cd-receiver
SERVICE_USER=deploy

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Los prerequisitos se comprueban TODOS aqui arriba, antes de crear usuarios,
# contenedores o directorios: es preferible negarse a empezar que morir a
# mitad de camino y dejar el sistema a medio instalar.
[[ $EUID -eq 0 ]] || die "ejecutalo con sudo."
command -v docker >/dev/null || die "Docker no esta instalado."
docker compose version >/dev/null 2>&1 || die "falta el plugin 'docker compose'."
command -v python3 >/dev/null || die "python3 no esta instalado."
command -v rsync   >/dev/null || die "rsync no esta instalado (lo usa el despliegue del codigo)."
command -v openssl >/dev/null || die "openssl no esta instalado (genera el secreto del webhook)."

# python3 a secas no basta: en Debian y Ubuntu el modulo venv viaja en un
# paquete aparte. Sin el, la instalacion moria justo al crear el entorno
# virtual, con el receptor ya copiado y el usuario ya creado.
# Se comprueba `import ensurepip`, NO `venv --help`: el segundo devuelve 0
# aunque falte el paquete, porque mostrar la ayuda no necesita ensurepip. Es
# ensurepip lo que crea pip dentro del entorno, y su ausencia es justo lo que
# hace fracasar la creacion del venv.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    if command -v apt-get >/dev/null; then
        log "falta ensurepip (paquete python3-venv); instalandolo"
        apt-get update -qq
        apt-get install -y -qq python3-venv
        python3 -c "import ensurepip" >/dev/null 2>&1             || die "se instalo python3-venv pero ensurepip sigue sin estar disponible."
    else
        die "falta ensurepip. Instala el paquete python3-venv de tu distribucion."
    fi
fi

# --- usuario de servicio -----------------------------------------------------
# Sin shell de login y sin home propio: solo existe para correr el servicio.
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    log "creando el usuario de servicio $SERVICE_USER"
    useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" "$SERVICE_USER"
fi

# Deliberadamente NO se anade al grupo docker (ADR-0005): el receptor llega a
# la API por el socket-proxy. Si 'deploy' aparece alguna vez en ese grupo es
# una regresion de seguridad, asi que se retira.
if id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx docker; then
    log "AVISO: $SERVICE_USER estaba en el grupo docker; se retira (ADR-0005)"
    gpasswd -d "$SERVICE_USER" docker || true
fi

# --- socket-proxy: unico contenedor con acceso al socket de Docker ---
log "desplegando el socket-proxy de la API de Docker"
install -d -o root -g root -m 0755 "$PROXY_DIR"
install -m 0644 "$REPO_DIR/deploy/docker-socket-proxy.yml" "$PROXY_DIR/docker-compose.yml"
docker compose -f "$PROXY_DIR/docker-compose.yml" -p cd-socket-proxy up -d

# El contenedor tarda un instante en aceptar conexiones: comprobar justo
# despues de 'up -d' daba un falso negativo. Se reintenta antes de rendirse.
log "esperando a que el socket-proxy acepte conexiones"
proxy_listo=0
for _ in $(seq 1 30); do
    if DOCKER_HOST=tcp://127.0.0.1:2375 docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
        proxy_listo=1
        break
    fi
    sleep 1
done
if [[ $proxy_listo -eq 0 ]]; then
    echo "--- ultimos logs del socket-proxy ---" >&2
    docker logs --tail 15 cd-socket-proxy 2>&1 | sed 's/^/  /' >&2
    die "el socket-proxy no responde en 127.0.0.1:2375 tras 30s"
fi
log "socket-proxy operativo"

# --- directorios -------------------------------------------------------------
log "preparando directorios"
install -d -o root          -g root          -m 0755 "$CONF_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$STATE_DIR" "$LOG_DIR"
install -d -o root          -g root          -m 0755 /srv/apps

# --- codigo ------------------------------------------------------------------
log "copiando el receptor a $APP_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$APP_DIR"
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
    "$REPO_DIR/receiver/" "$APP_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

log "instalando dependencias en el entorno virtual"
# Se comprueba pip, no python: un venv creado sin ensurepip tiene el interprete
# pero no pip, y mirar solo python daba por bueno un entorno inservible. Si
# esta incompleto se rehace, que es barato y deja el estado limpio.
if [[ ! -x "$APP_DIR/.venv/bin/pip" ]]; then
    [[ -e "$APP_DIR/.venv" ]] && log "el entorno virtual estaba incompleto; se rehace"
    rm -rf "$APP_DIR/.venv"
    sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- configuracion -----------------------------------------------------------
# Nunca se sobrescribe: el secreto y el inventario son del operador.
if [[ ! -f "$CONF_DIR/receiver.env" ]]; then
    log "generando $CONF_DIR/receiver.env con un secreto nuevo"
    secret="$(openssl rand -hex 32)"
    sed "s|^WEBHOOK_SECRET=.*|WEBHOOK_SECRET=$secret|" \
        "$REPO_DIR/.env.example" > "$CONF_DIR/receiver.env"
    chmod 600 "$CONF_DIR/receiver.env"
    chown root:root "$CONF_DIR/receiver.env"
    echo
    printf '\033[1;33mSECRETO DEL WEBHOOK (pegalo en GitHub > Settings > Webhooks):\033[0m\n'
    printf '  %s\n\n' "$secret"
else
    log "$CONF_DIR/receiver.env ya existe, se conserva"
fi

if [[ ! -f "$CONF_DIR/apps.yml" ]]; then
    log "creando $CONF_DIR/apps.yml a partir del ejemplo (EDITALO antes de usarlo)"
    install -o root -g "$SERVICE_USER" -m 0640 \
        "$REPO_DIR/config/apps.example.yml" "$CONF_DIR/apps.yml"
else
    log "$CONF_DIR/apps.yml ya existe, se conserva"
fi

# --- servicio ----------------------------------------------------------------
log "instalando la unidad de systemd"
install -m 0644 "$REPO_DIR/deploy/cd-receiver.service" /etc/systemd/system/cd-receiver.service
systemctl daemon-reload
systemctl enable cd-receiver.service
systemctl restart cd-receiver.service

sleep 2
if systemctl is-active --quiet cd-receiver.service; then
    log "servicio activo"
    curl -fsS http://127.0.0.1:9000/health && echo
else
    die "el servicio no arranco. Revisa: journalctl -u cd-receiver -n 50"
fi

cat <<'NEXT'

Siguientes pasos:
  1. Edita /etc/cd-receiver/apps.yml con tus aplicaciones reales.
     Recuerda: apps.yml NO se versiona (el repo es publico).
  2. Recarga:  curl -X POST http://127.0.0.1:9000/reload
  3. Publica el receptor con el tunel (ver ingress/README.md).
  4. En GitHub: Settings > Webhooks > Add webhook
       Payload URL:  https://deploy.tudominio.com/webhook
       Content type: application/json
       Secret:       el mostrado arriba
       Events:       "Let me select individual events" > Workflow runs
NEXT
