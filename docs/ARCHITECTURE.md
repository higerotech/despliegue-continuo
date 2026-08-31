# Arquitectura y decisiones

## Contexto

Desplegar aplicaciones en un servidor Linux propio, disparado desde GitHub,
usando webhooks. El servidor no tiene IP publica utilizable y ya existe una
capa de tunel (Cloudflare / Tailscale).

## Alternativas consideradas

| Opcion | Por que no se eligio |
|---|---|
| **Runner self-hosted de Actions** | Tecnicamente lo mas simple (sin ingress, logs en GitHub), pero el runner clona y ejecuta codigo del repo en el servidor: un PR malicioso se convierte en ejecucion local. Ademas el disparo es *pull*, no webhook. |
| **PaaS self-hosted (Coolify, Dokploy)** | Resuelve todo de golpe, pero es una pieza grande y opaca que hay que mantener y actualizar; el control fino sobre el despliegue se pierde. |
| **Watchtower / polling de GHCR** | Sin trazabilidad de quien desplegó qué ni cuándo, sin healthcheck ni rollback, y con latencia igual al intervalo de sondeo. |
| **Port forwarding + nginx + Let's Encrypt** | Expone superficie real en el router y añade renovacion de certificados. El tunel cubre lo mismo sin nada de eso. |

**Elegido:** webhook firmado → receptor propio → `docker compose`. Da control
total sobre el proceso de despliegue, cero exposicion de red y un volumen de
codigo pequeño (unas 600 lineas) que se puede leer entero.

## Decisiones

### D1 — El disparador es `workflow_run`, no `push`

Un `push` llega antes de que la imagen exista y sin informacion sobre los
tests. `workflow_run` con `conclusion == success` garantiza que la imagen esta
publicada y que la build paso. `push` sigue soportado (`event: push` en
`apps.yml`) para apps que se construyen en el propio servidor.

**Coste:** el evento `workflow_run` se emite para *todos* los workflows del
repo, de ahi el filtro por nombre (`workflow: build`).

### D2 — Tag inmutable por SHA

`docker compose` recibe `IMAGE_TAG=sha-1a2b3c4`. Nunca `latest`.

Esto hace que el estado sea legible (`docker ps` dice exactamente qué commit
corre) y convierte el rollback en una operacion trivial: el tag anterior sigue
existiendo en GHCR. El tag anterior se persiste en
`/var/lib/cd-receiver/<app>.json` en cada despliegue con exito.

### D3 — El receptor solo escucha en loopback

`BIND_HOST=127.0.0.1`. El unico camino desde internet es el tunel, y su
configuracion publica solo la ruta `/webhook`. `/status` y `/reload` exigen
origen loopback o `STATUS_TOKEN`.

### D4 — Encolar y responder 202

GitHub corta la entrega a los ~10 segundos y la marca como fallida. Un
despliegue tarda mucho mas. El endpoint valida, encola y responde; un worker
por app hace el trabajo. Efecto util: dos pushes seguidos a la misma app se
despliegan en orden en vez de pisarse.

### D5 — La respuesta 202 lleva el motivo

Cuando un evento no dispara despliegue, la respuesta explica por qué. GitHub
guarda el cuerpo en *Recent Deliveries*, asi que el diagnostico esta en la UI
sin necesidad de entrar por SSH.

### D6 — El inventario es la allowlist

Solo se despliega lo declarado en `apps.yml`, y el emparejamiento es por
`repo` + `branch` + `event` + `workflow`. `project_dir` viene de ese fichero,
nunca del payload.

## Modelo de amenazas

| Amenaza | Mitigacion | Riesgo residual |
|---|---|---|
| Peticion falsificada al endpoint | HMAC-SHA256 sobre el cuerpo crudo con `compare_digest`; se valida antes de parsear el JSON | Ninguno mientras el secreto no se filtre |
| Fuga del secreto del webhook | `receiver.env` en `root:root 0600`; el instalador lo genera con `openssl rand -hex 32` | Rotar en GitHub y en el servidor si se sospecha |
| Reentrega o replay | Cache LRU de `X-GitHub-Delivery` (1024 entradas) | Un replay tras 1024 entregas distintas volveria a desplegar el mismo SHA: idempotente en la practica |
| Inyeccion de comandos por el payload | `create_subprocess_exec` con lista de argumentos, sin shell; ninguna ruta sale del payload | Ninguno |
| Despliegue de un repo ajeno | Emparejamiento estricto contra el inventario | Ninguno |
| Escalada desde el proceso receptor | Usuario `deploy` sin shell; systemd con `ProtectSystem=strict`, `NoNewPrivileges`, `ReadWritePaths` acotado | **El grupo `docker` equivale a root.** Es inherente a desplegar contenedores sin sudo. Para eliminarlo: Docker rootless o un socket-proxy con API restringida |
| Denegacion de servicio | Limite de cuerpo (1 MiB), cola acotada por worker, `command_timeout` | Un atacante sin el secreto solo consigue 401 |
| Despliegue de una build rota | Healthcheck con timeout + rollback automatico al tag anterior | Sin `health_url` no hay verificacion: **declararla siempre** |
| Exposicion de `/status` | Loopback o token | Ninguno si el tunel filtra por ruta |

## Limites conocidos

- **No hay despliegue multi-servidor.** Un receptor por servidor; el mismo
  webhook puede apuntar a varios si se declara varias veces en GitHub.
- **No hay migraciones de base de datos.** Si una app las necesita, el sitio
  natural es un servicio `migrate` en el compose con `depends_on`.
- **El rollback restaura el contenedor, no los datos.** Una migracion aplicada
  no se revierte sola.
- **Sin despliegue sin cortes.** `docker compose up -d` recrea el contenedor;
  hay unos segundos de indisponibilidad. Para evitarlo haría falta un proxy
  delante con dos réplicas.

## Si esto crece

En orden de utilidad real:

1. **Notificaciones** del resultado a Slack/Telegram desde `queue._record`.
2. **`health_url` obligatoria** en la validacion de `apps.yml`.
3. **Podas de imagenes** (`docker image prune`) por temporizador de systemd.
4. **Docker rootless o socket-proxy** para quitar el grupo `docker`.
5. **Despliegue sin cortes** con Traefik y dos réplicas por app.
