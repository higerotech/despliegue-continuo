# Runbook — Instalación y operación del receptor

* **Estado:** approved
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.3.0
* **Gate:** 2
* **Servicio:** `cd-receiver.service` + stack `cd-socket-proxy`
* **Host:** servidor Linux con systemd y Docker

## Instalación

```bash
git clone https://github.com/higerotech/despliegue-continuo /tmp/cd
sudo /tmp/cd/deploy/install.sh
```

El instalador es **idempotente**: se puede reejecutar para actualizar el código sin tocar el
secreto ni el inventario. Lo que hace, en orden:

1. Crea el usuario de sistema `deploy` **sin shell** y —deliberadamente— **fuera del grupo
   `docker`**. Si lo encuentra en ese grupo, lo retira: es una regresión (ADR-0005).
2. Despliega el socket-proxy en `/srv/infra/socket-proxy/` y **comprueba que responde** antes
   de continuar.
3. Copia el receptor a `/opt/cd-receiver`, crea el entorno virtual e instala dependencias.
4. Genera `/etc/cd-receiver/receiver.env` con un secreto de `openssl rand -hex 32` y **lo
   imprime una sola vez**. Si el fichero ya existe, no lo toca.
5. Instala y arranca la unidad de systemd, y verifica `/health`.

### Después de instalar

```bash
sudoedit /etc/cd-receiver/apps.yml          # declara tus aplicaciones
curl -X POST http://127.0.0.1:9000/reload   # sin reiniciar el servicio
```

## Alta de una aplicación nueva

1. **En el servidor**, crear `/srv/apps/<app>/docker-compose.yml` a partir de
   `templates/docker-compose.yml`. La imagen **debe** usar `${IMAGE_TAG}`.
2. **En el repositorio de la app**, copiar `templates/build-and-push.yml` a
   `.github/workflows/build-and-push.yml`.
3. **Declarar en el inventario** y recargar con `POST /reload`.
4. **Registrar el webhook** en GitHub — `Settings → Webhooks → Add webhook`:

   | Campo | Valor |
   |---|---|
   | Payload URL | `https://deploy.<tu-dominio>/webhook` |
   | Content type | `application/json` |
   | Secret | el que imprimió el instalador |
   | Events | *Let me select individual events* → **Workflow runs** |

   O con `gh`:

   ```bash
   gh api repos/higerotech/<app>/hooks -X POST \
     -f name=web -F active=true -f 'events[]=workflow_run' \
     -f config[url]=https://deploy.<tu-dominio>/webhook \
     -f config[content_type]=json \
     -f config[secret]="$WEBHOOK_SECRET"
   ```

5. **Verificar** con un push a `main` y `GET /status`.

## Operación diaria

```bash
# Que version corre, que hay en cola, ultimos despliegues
curl -s http://127.0.0.1:9000/status | jq

# Log del servicio en vivo
journalctl -u cd-receiver -f

# Historico legible de una aplicacion
jq -r '[.started_at, .tag, (if .ok then "OK" else "FALLO" end)] | @tsv' \
    /var/log/cd-receiver/mi-api.jsonl | tail -20

# Estado del socket-proxy
docker compose -f /srv/infra/socket-proxy/docker-compose.yml ps
```

## Diagnóstico: el webhook no despliega

El receptor responde `202` **con el motivo**, y GitHub lo guarda en
*Settings → Webhooks → Recent Deliveries*. **Mira ahí primero**: casi siempre evita entrar por
SSH.

| Respuesta en GitHub | Causa | Acción |
|---|---|---|
| `401 firma invalida` | El secreto de GitHub y el de `receiver.env` no coinciden | Reponer el secreto en el webhook, o rotar en ambos lados |
| `202 ignored: el repo X no esta en el inventario` | Falta la entrada, o el nombre no coincide | Añadir a `apps.yml` y `POST /reload` |
| `202 ignored: rama 'X' distinta...` | El push no fue a la rama desplegable | Correcto: no hay nada que hacer |
| `202 ignored: workflow 'X' distinto...` | Terminó otro workflow, o se renombró el de build | Alinear `workflow:` en `apps.yml` con el `name:` del workflow |
| `202 ignored: workflow_run conclusion='failure'` | La build falló | Correcto: arreglar la build |
| `202 ignored: delivery ... ya procesado` | Reentrega de GitHub | Correcto. Para forzar, usar *Redeliver* tras reiniciar el servicio |
| `413 payload demasiado grande` | Payload por encima de 1 MiB | Muy improbable; revisar `MAX_BODY_BYTES` |
| Sin respuesta / timeout | El túnel está caído | `systemctl status cloudflared` |

## Diagnóstico: el despliegue se encoló pero falló

**El resultado del despliegue no vuelve a GitHub** (deuda DS-03): la entrega figura correcta
aunque el despliegue fallara. Hay que mirarlo en el servidor.

```bash
curl -s http://127.0.0.1:9000/status | jq '.history[0]'
```

| Error en `error` | Causa probable | Acción |
|---|---|---|
| `denied` / `manifest unknown` al hacer `pull` | El tag no existe en GHCR | Comprobar que `tag_template` coincide con lo que publica `type=sha` |
| `healthcheck agotado tras Ns` | La app no arranca o `health_url` es incorrecta | `docker compose logs` de la app; verificar la URL desde el host |
| `Cannot connect to the Docker daemon` | El socket-proxy no está en pie | `docker compose -f /srv/infra/socket-proxy/docker-compose.yml up -d` |
| `no existe <ruta>/docker-compose.yml` | `project_dir` mal declarado | Corregir `apps.yml` y recargar |
| `timeout tras 600s` | `pull` muy lento o imagen enorme | Subir `command_timeout` en la app |

Si `rolled_back: true`, el servicio **ya está restaurado** en la versión anterior; el arreglo
no es urgente. Si `rolled_back: false` y `ok: false`, el servicio puede estar caído.

## Procedimientos

### Rollback manual

```bash
cd /srv/apps/mi-api
IMAGE_TAG=sha-1a2b3c4 docker compose up -d
```

El tag anterior está en `/var/lib/cd-receiver/mi-api.json` (`previous_tag`).

### Rotación del secreto del webhook

Cubre la deuda **DS-04**. Hay una ventana en la que las entregas fallan; hacerlo con calma.

```bash
NUEVO=$(openssl rand -hex 32)
sudo sed -i "s|^WEBHOOK_SECRET=.*|WEBHOOK_SECRET=$NUEVO|" /etc/cd-receiver/receiver.env
sudo systemctl restart cd-receiver
echo "$NUEVO"   # actualizar en el webhook de CADA repositorio
```

Después, actualizar el secreto en todos los webhooks y comprobar con *Redeliver* que una
entrega vuelve a dar `202`.

### Parada controlada

```bash
sudo systemctl stop cd-receiver   # espera a los despliegues en curso (drain, 30 s)
```

### Actualización del receptor

```bash
cd /tmp/cd && git pull && sudo ./deploy/install.sh
```

## Verificación de invariantes de seguridad

Comprobar tras cualquier cambio en el host:

```bash
# 1. El usuario deploy NO debe estar en el grupo docker
id -nG deploy | tr ' ' '\n' | grep -qx docker && echo "FALLO ADR-0005" || echo "OK"

# 2. Nada del receptor escucha en interfaz publica
ss -ltnp | grep -E '0\.0\.0\.0:(9000|2375)' && echo "FALLO RNF01" || echo "OK"

# 3. El secreto solo lo lee root
stat -c '%a %U:%G' /etc/cd-receiver/receiver.env   # esperado: 600 root:root

# 4. exec sigue bloqueado en el proxy
DOCKER_HOST=tcp://127.0.0.1:2375 docker exec $(docker ps -q | head -1) ls / 2>&1 | grep -q 403 \
  && echo "OK" || echo "REVISAR: exec no esta bloqueado"
```

## Escalado a incidente

| Señal | Gravedad | Primera acción |
|---|---|---|
| `401` repetidos desde una IP que no es GitHub | Alta | Revisar entregas en GitHub; considerar regla WAF en Cloudflare |
| Un despliegue no solicitado en `/status` | **Crítica** | Asumir secreto comprometido: rotar de inmediato y auditar el `.jsonl` |
| El socket-proxy caído y no rearranca | Media | Los despliegues se detienen; las apps siguen corriendo. Sin urgencia |
| Contenedor desconocido en `docker ps` | **Crítica** | Posible materialización de T4. Aislar el host y auditar |
