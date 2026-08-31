# despliegue-continuo

Despliegue continuo desde GitHub a un servidor Linux propio, mediante webhooks
firmados, sin abrir ni un puerto en el router.

GitHub Actions construye y publica la imagen; un receptor propio que escucha en
el servidor valida la firma del webhook y despliega el commit exacto, con
healthcheck y rollback automatico.

```
push a main
    |
    v
GitHub Actions ──build──> GHCR (ghcr.io/higerotech/app:sha-1a2b3c4)
    |
    | evento workflow_run (firmado con HMAC-SHA256)
    v
Cloudflare Tunnel ──conexion saliente desde el servidor──> 127.0.0.1:9000
    |
    v
Receptor  ──valida firma──> cola por app ──> docker compose pull + up -d
                                                   |
                                            healthcheck
                                             /          \
                                          ok             falla
                                           |               |
                                      guarda tag      rollback al tag anterior
```

## Por que asi

**El despliegue se dispara con `workflow_run`, no con `push`.** Un `push`
llega antes de que exista la imagen y sin saber si los tests pasaron. Esperar a
que el workflow de build termine con `conclusion: success` significa que solo
se despliega lo que ya esta construido y verificado.

**El tag es el SHA del commit, nunca `latest`.** Cada despliegue es
reproducible, y el rollback es volver a levantar el tag anterior, que sigue en
el registro. Con `latest` no se sabe que hay corriendo ni a donde volver.

**El receptor escucha solo en `127.0.0.1`.** Quien lo expone es el tunel, que
abre una conexion saliente. No hay puertos abiertos, ni IP fija, ni
certificados que renovar. Y el tunel publica unicamente `/webhook`: `/status` y
`/reload` no existen desde internet.

**El receptor no pertenece al grupo `docker`.** Habla con la API a traves de
un socket-proxy que solo expone lo que `docker compose` necesita: `exec`,
`secrets`, `swarm` y `build` devuelven `403`. Esto **reduce** la superficie,
pero no elimina la equivalencia a root, porque crear contenedores sigue siendo
imprescindible. Esta razonado sin adornos en ADR-0005.

**El inventario `apps.yml` es la allowlist.** Un webhook de un repo que no este
declarado se ignora. Nada del payload llega nunca a un shell: los comandos se
ejecutan con `exec` y lista de argumentos.

## Estructura

| Ruta | Que es |
|---|---|
| `receiver/app/security.py` | Verificacion HMAC y cache de reentregas |
| `receiver/app/events.py` | Payload de GitHub a intencion de despliegue |
| `receiver/app/config.py` | Ajustes de entorno e inventario de apps |
| `receiver/app/deployer.py` | Pull, arranque, healthcheck y rollback |
| `receiver/app/queue.py` | Un worker por app, despliegues serializados |
| `receiver/app/main.py` | Endpoints `/webhook`, `/health`, `/status`, `/reload` |
| `config/apps.example.yml` | Plantilla del inventario |
| `deploy/install.sh` | Instalador para el servidor |
| `deploy/cd-receiver.service` | Unidad systemd endurecida |
| `deploy/docker-socket-proxy.yml` | API de Docker recortada; evita el grupo `docker` |
| `ingress/` | Cloudflare Tunnel y Tailscale Funnel |
| `templates/` | Workflow y compose para el repo de cada app |

## Puesta en marcha

### 1. Servidor

```bash
git clone https://github.com/higerotech/despliegue-continuo /tmp/cd
sudo /tmp/cd/deploy/install.sh
```

El instalador crea el usuario `deploy`, genera el secreto del webhook y lo
muestra por pantalla, deja el servicio activo y comprueba `/health`.

### 2. Cada aplicacion

En el servidor, `/srv/apps/<app>/docker-compose.yml` a partir de
`templates/docker-compose.yml`. La imagen debe usar `${IMAGE_TAG}`, nunca un
tag fijo.

Al ser publicos los paquetes de GHCR, el `pull` es anonimo y **no hace falta
`docker login`** en el servidor. Si alguna aplicacion pasara a tener imagen
privada, habria que autenticar el daemon (no el usuario `deploy`, que ya no
habla directamente con Docker).

Declara la app en `/etc/cd-receiver/apps.yml` y recarga sin reiniciar:

```bash
curl -X POST http://127.0.0.1:9000/reload
```

En el repo de la app, copia `templates/build-and-push.yml` a
`.github/workflows/build-and-push.yml`.

### 3. Ingress

Ver `ingress/README.md`. Con Cloudflare Tunnel:

```bash
cloudflared tunnel create cd-receiver
cloudflared tunnel route dns cd-receiver deploy.tudominio.com
sudo cloudflared service install
```

### 4. Webhook en GitHub

En cada repo de aplicacion, `Settings > Webhooks > Add webhook`:

| Campo | Valor |
|---|---|
| Payload URL | `https://deploy.tudominio.com/webhook` |
| Content type | `application/json` |
| Secret | el que genero el instalador |
| Events | *Let me select individual events* → **Workflow runs** |

O con `gh`:

```bash
gh api repos/higerotech/mi-api/hooks -X POST \
  -f name=web -F active=true -f 'events[]=workflow_run' \
  -f config[url]=https://deploy.tudominio.com/webhook \
  -f config[content_type]=json \
  -f config[secret]="$WEBHOOK_SECRET"
```

## Operacion

```bash
# Que version esta viva, que hay en cola, ultimos despliegues
curl -s http://127.0.0.1:9000/status | jq

# Log del servicio
journalctl -u cd-receiver -f

# Historico por aplicacion
jq -r '[.started_at, .tag, (if .ok then "OK" else "FALLO" end)] | @tsv' \
    /var/log/cd-receiver/mi-api.jsonl | tail -20
```

**Rollback manual** a una version anterior:

```bash
cd /srv/apps/mi-api && IMAGE_TAG=sha-1a2b3c4 docker compose up -d
```

**Diagnostico de un webhook que no despliega.** El receptor responde `202` con
el motivo, y GitHub lo muestra en *Settings → Webhooks → Recent Deliveries*:

| Respuesta | Significado |
|---|---|
| `401 firma invalida` | El secreto de GitHub y el de `receiver.env` no coinciden |
| `202 ignored: el repo X no esta en el inventario` | Falta la entrada en `apps.yml` o el nombre no coincide |
| `202 ignored: rama 'develop' distinta...` | La rama no es la desplegable |
| `202 ignored: workflow 'tests' distinto...` | Termino otro workflow, no el de build |
| `202 ignored: workflow_run conclusion='failure'` | La build fallo; correcto no desplegar |
| Sin respuesta / timeout | El tunel esta caido: `systemctl status cloudflared` |

## Desarrollo

```bash
cd receiver
python -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/pytest -q
```

Para probar en local sin GitHub, firma tu mismo la peticion:

```bash
BODY='{"action":"completed","workflow_run":{"head_branch":"main","head_sha":"1a2b3c4d5e6f78901234567890abcdef12345678","conclusion":"success","name":"build"},"repository":{"full_name":"higerotech/mi-api"}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')
curl -si http://127.0.0.1:9000/webhook \
  -H "X-GitHub-Event: workflow_run" \
  -H "X-GitHub-Delivery: $(uuidgen)" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  -H 'Content-Type: application/json' \
  -d "$BODY"
```

## Documentación

El proyecto sigue **AI-DLC**. Índice de navegación en
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); los documentos que más se consultan:

| Documento | Para qué |
|---|---|
| [`docs/02-design/architecture.md`](docs/02-design/architecture.md) | Diagramas C4, secuencia del flujo crítico y ciclo de vida del despliegue |
| [`docs/02-design/threat-model.md`](docs/02-design/threat-model.md) | STRIDE, DREAD y riesgo residual |
| [`docs/00-project/adr/`](docs/00-project/adr/) | Por qué cada decisión, con sus alternativas |
| [`docs/03-implementation/deployment-runbook.md`](docs/03-implementation/deployment-runbook.md) | Instalar, operar y diagnosticar |
| [`docs/04-testing/test-strategy.md`](docs/04-testing/test-strategy.md) | Qué está probado y qué no |

**Estado de los gates:** 0 y 1 superados; **2 y 3 abiertos** (faltan SAST, SCA, cobertura
medida, pruebas e2e y DAST). El detalle está en [`.ai-dlc/gates/`](.ai-dlc/gates/).
