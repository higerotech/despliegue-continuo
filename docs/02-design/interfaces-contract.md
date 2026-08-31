# Contrato de interfaces

* **Estado:** approved
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.2.0
* **Gate:** 1
* **Interfaces cubiertas:** HTTP del receptor · esquema de `apps.yml` · contrato con el repositorio de cada aplicación

## 1. Interfaz HTTP del receptor

Solo `POST /webhook` se publica por el túnel. El resto vive en loopback (ADR-0004).

```yaml
openapi: 3.1.0
info:
  title: CD Receiver
  version: "1.0.0"
  description: |
    Receptor de webhooks de GitHub que dispara despliegues locales.
    La autenticacion de /webhook es la firma HMAC-SHA256 de GitHub sobre el
    cuerpo crudo; no hay sesiones ni tokens de usuario.
servers:
  - url: https://deploy.EJEMPLO.com
    description: Publicado por el tunel; solo expone /webhook
  - url: http://127.0.0.1:9000
    description: Acceso local en el host

paths:
  /webhook:
    post:
      summary: Recibe un evento de GitHub y encola un despliegue si procede
      operationId: receiveWebhook
      parameters:
        - { name: X-GitHub-Event,       in: header, required: true,  schema: { type: string, examples: [workflow_run] } }
        - { name: X-GitHub-Delivery,    in: header, required: true,  schema: { type: string, format: uuid } }
        - { name: X-Hub-Signature-256,  in: header, required: true,  schema: { type: string, pattern: '^sha256=[0-9a-f]{64}$' } }
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              description: Payload de GitHub. Solo se leen repository.full_name, la rama y el SHA.
      responses:
        "202":
          description: |
            Aceptado. Cubre DOS casos distintos, discriminados por `status`:
            `queued` (se desplegara) e `ignored` (no procede, con el motivo).
          content:
            application/json:
              schema:
                oneOf:
                  - $ref: "#/components/schemas/Queued"
                  - $ref: "#/components/schemas/Ignored"
        "400": { description: JSON invalido }
        "401": { description: Firma ausente o invalida }
        "413": { description: Cuerpo mayor que MAX_BODY_BYTES (1 MiB por defecto) }

  /health:
    get:
      summary: Sonda de vida, sin autenticacion y sin revelar informacion
      operationId: health
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties: { status: { type: string, const: ok } }

  /status:
    get:
      summary: Estado de las aplicaciones, cola e historico
      operationId: status
      description: Requiere origen loopback o `Authorization: Bearer $STATUS_TOKEN`.
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Status" }
        "403": { description: No autorizado }

  /reload:
    post:
      summary: Relee apps.yml sin reiniciar ni cortar despliegues en curso
      operationId: reload
      description: Mismo control de acceso que /status.
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  status: { type: string, const: reloaded }
                  apps:   { type: array, items: { type: string } }
        "400": { description: El inventario no es valido; se conserva el anterior }
        "403": { description: No autorizado }

components:
  schemas:
    Queued:
      type: object
      required: [status, app, sha, tag, position]
      properties:
        status:   { type: string, const: queued }
        app:      { type: string, examples: [mi-api] }
        sha:      { type: string, minLength: 40, maxLength: 40 }
        tag:      { type: string, examples: [sha-1a2b3c4] }
        position: { type: integer, description: Trabajos por delante en la cola de esa app }
    Ignored:
      type: object
      required: [status, reason]
      properties:
        status: { type: string, const: ignored }
        reason:
          type: string
          description: Motivo legible; GitHub lo muestra en Recent Deliveries
          examples:
            - "rama 'develop' distinta de la desplegable ('main')"
            - "workflow_run conclusion='failure'"
            - "el repo owner/otro no esta en el inventario de apps"
            - "delivery 7f3a... ya procesado"
    Status:
      type: object
      properties:
        apps:
          type: object
          additionalProperties:
            type: object
            properties:
              repo:         { type: string }
              branch:       { type: string }
              event:        { type: string }
              current_tag:  { type: string }
              previous_tag: { type: [string, "null"] }
              updated_at:   { type: string, format: date-time }
        running: { type: object, description: Despliegue en curso por aplicacion }
        pending: { type: object, description: Trabajos en cola por aplicacion }
        history:
          type: array
          description: Ultimos despliegues (HISTORY_SIZE, 50 por defecto)
          items: { $ref: "#/components/schemas/DeployResult" }
    DeployResult:
      type: object
      properties:
        app:         { type: string }
        sha:         { type: string }
        tag:         { type: string }
        ok:          { type: boolean }
        rolled_back: { type: boolean }
        started_at:  { type: string, format: date-time }
        seconds:     { type: number }
        error:       { type: string }
        steps:
          type: array
          items:
            type: object
            properties:
              name:    { type: string }
              ok:      { type: boolean }
              seconds: { type: number }
              detail:  { type: string }
```

### Contrato de errores

| Código | Cuándo | Por qué ese código |
|---|---|---|
| `202 queued` | El despliegue se encoló | GitHub exige respuesta rápida; el trabajo es asíncrono (ADR-0008) |
| `202 ignored` | Evento legítimo que no debe desplegar | **No es un error.** Un `4xx` haría que GitHub reintentase y marcase el webhook como defectuoso |
| `400` | El cuerpo no es JSON válido | Solo se llega aquí con firma válida: indica un problema real, no un ataque |
| `401` | Firma ausente, malformada o incorrecta | Único código que denota hostilidad; se registra con `WARNING` |
| `413` | Cuerpo por encima del límite | Se comprueba **antes** de firmar o parsear |
| `403` | `/status` o `/reload` sin autorización | No debería ocurrir: el túnel ya filtra por ruta |

## 2. Esquema del inventario `apps.yml`

Fichero en `/etc/cd-receiver/apps.yml`, `0640 root:deploy`, **no versionado** (ADR-0007).

| Campo | Tipo | Obligatorio | Defecto | Significado |
|---|---|---|---|---|
| `name` | string | **sí** | — | Identificador y nombre del proyecto compose |
| `repo` | string | **sí** | — | `owner/repo`; se normaliza a minúsculas |
| `project_dir` | ruta | **sí** | — | Directorio del `docker-compose.yml` en el servidor |
| `image` | string | **sí** | — | Imagen; se expone como `$IMAGE`. Informativo |
| `branch` | string | no | `main` | Única rama desplegable |
| `event` | enum | no | `workflow_run` | `workflow_run` \| `push` |
| `workflow` | string | no | — | Nombre del workflow que debe haber pasado |
| `compose_file` | string | no | `docker-compose.yml` | Relativo a `project_dir` |
| `tag_template` | string | no | `sha-{short_sha}` | Admite `{sha}` y `{short_sha}` |
| `services` | lista | no | `[]` | Vacío = `pull` de todos los servicios |
| `health_url` | URL | no | — | **Sin ella no hay verificación ni rollback fiable** (DS-06) |
| `health_timeout` | int (s) | no | `90` | Margen del healthcheck |
| `health_interval` | float (s) | no | `3.0` | Intervalo entre sondeos |
| `rollback` | bool | no | `true` | Restaurar el tag anterior al fallar |
| `command_timeout` | int (s) | no | `600` | Límite por comando de `docker compose` |

**Invariantes que el cargador impone** (`config.load_apps`): un repo no puede aparecer dos
veces; `event` solo admite los dos valores; si falta un campo obligatorio el arranque falla en
lugar de degradarse.

## 3. Contrato con el repositorio de cada aplicación

Tres obligaciones. Incumplir cualquiera hace que la aplicación no se despliegue.

| # | Obligación | Verificación |
|---|---|---|
| 1 | Un workflow cuyo **nombre** coincida con `workflow` del inventario, que publique en GHCR con `docker/metadata-action` y `type=sha` | `202 ignored: workflow 'X' distinto del esperado` si no coincide |
| 2 | El `docker-compose.yml` del servidor referencia `${IMAGE_TAG}`, **nunca** un tag literal | La plantilla usa `${IMAGE_TAG:?}` para fallar de forma ruidosa |
| 3 | El servicio expone un endpoint de salud accesible desde el host | Sin él, el despliegue se da por bueno en cuanto arranca el contenedor |

El formato de tag es el acoplamiento más frágil entre las dos partes: `type=sha` produce
`sha-` + 7 caracteres, que es exactamente el `tag_template` por defecto. **Si se cambia uno,
hay que cambiar el otro**, o el `pull` fallará con `manifest unknown`.

Plantillas listas para copiar: `templates/build-and-push.yml` y `templates/docker-compose.yml`.
