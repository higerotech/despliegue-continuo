# Glosario / Lenguaje Ubicuo (DDD)

* **Estado:** approved
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.1.0
* **Contextos acotados:** Recepción de eventos · Orquestación de despliegue · Ingress

El código usa estos términos exactamente con este significado. Cuando el vocabulario de GitHub
y el nuestro difieren, la columna de definición lo aclara.

## Contexto acotado: Recepción de eventos

| Término | Definición | Dónde vive |
|---|---|---|
| **Entrega** (delivery) | Un envío concreto de GitHub, identificado por `X-GitHub-Delivery`. GitHub reintenta las fallidas, por eso una entrega repetida no debe volver a desplegar. | `security.DeliveryCache` |
| **Firma** | HMAC-SHA256 del cuerpo crudo con el secreto compartido, en `X-Hub-Signature-256`. Es la **única** prueba de origen del sistema. | `security.verify_signature` |
| **Intención de despliegue** (`DeployIntent`) | Lo único que el resto del sistema necesita del webhook: repo, rama, SHA, evento y workflow. Aísla al orquestador del formato de GitHub. | `events.DeployIntent` |
| **Evento ignorado** | Webhook legítimo y bien firmado que no debe desplegar (rama distinta, build fallida, repo no declarado). No es un error: se responde `202` con el motivo. | `events.IgnoredEvent` |

## Contexto acotado: Orquestación de despliegue

| Término | Definición | Dónde vive |
|---|---|---|
| **Aplicación desplegable** (`AppConfig`) | Una entrada del inventario. Define qué repo la dispara, dónde vive su `docker-compose.yml` y cómo se verifica su salud. | `config.AppConfig` |
| **Inventario** (`apps.yml`) | La lista de aplicaciones desplegables. **Es la allowlist**: lo que no está declarado no se despliega. Nunca se versiona. | `/etc/cd-receiver/apps.yml` |
| **Tag** | Etiqueta inmutable de la imagen, derivada del SHA del commit (`sha-1a2b3c4`). Nunca `latest`. Identifica sin ambigüedad qué código corre. | `AppConfig.tag_for` |
| **Tag anterior** | El tag del último despliegue con healthcheck correcto. Es el destino del rollback y se persiste en disco. | `/var/lib/cd-receiver/<app>.json` |
| **Trabajo** (`Job`) | Un despliegue encolado: la app, el SHA y la entrega que lo originó. | `queue.Job` |
| **Healthcheck** | Sondeo HTTP a `health_url` hasta `health_timeout`. Cualquier respuesta `< 400` da el despliegue por bueno. | `deployer._wait_healthy` |
| **Rollback** | Volver a levantar el **tag anterior** cuando el healthcheck falla. Restaura el contenedor, **nunca los datos**. | `deployer._rollback` |
| **Despliegue** | El ciclo completo: `pull` → `up -d` → healthcheck → (persistir tag \| rollback). | `deployer.deploy` |

## Contexto acotado: Ingress

| Término | Definición | Dónde vive |
|---|---|---|
| **Túnel** | Conexión saliente y persistente del host a Cloudflare. Sustituye a abrir puertos: nadie se conecta al servidor, el servidor se conecta a Cloudflare. | `cloudflared` en el host |
| **Socket-proxy** | Único contenedor con acceso a `/var/run/docker.sock`. Expone por loopback una API de Docker recortada, para que el receptor no necesite el grupo `docker`. | `deploy/docker-socket-proxy.yml` |
| **Loopback** | `127.0.0.1`. El receptor solo escucha ahí; quien lo publica hacia fuera es el túnel. | `BIND_HOST` |

## Diferencias con el vocabulario de GitHub

| GitHub dice | Nosotros decimos | Por qué importa |
|---|---|---|
| `push` | *No es un disparador de despliegue por defecto* | Un push llega antes de que exista la imagen (ADR-0002) |
| `workflow_run` | **Disparador** | Solo con `conclusion: success` hay algo construido que desplegar |
| `full_name` (`Owner/Repo`) | **Repo**, siempre en minúsculas | GitHub no distingue mayúsculas; el índice del inventario tampoco debe |
| "Deployment" (API de GitHub) | *No se usa* | No usamos la API de Deployments; el estado vive en el servidor |
