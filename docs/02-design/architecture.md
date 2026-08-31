# Arquitectura — Sistema de despliegue continuo

* **Estado:** approved
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.2.0
* **Gate:** 1
* **Estilo arquitectónico:** Clean Architecture ligera — el dominio (validar, emparejar,
  desplegar) no depende de FastAPI ni de Docker; ambos son detalles en los bordes.

## Principio rector

El sistema tiene **una sola frontera de confianza que importa**: la firma HMAC. Todo lo que la
cruza sin validarse es hostil; todo lo que la cruza validado se trata como una instrucción de
una fuente conocida, pero **aun así jamás se convierte en ruta ni en comando** (ADR-0007).

Consecuencia de diseño: la validación es lo primero que ocurre y es puramente funcional —
`security.verify_signature` no sabe qué es un despliegue, y `deployer.Deployer` no sabe qué es
un webhook.

## Contenedores

```mermaid
C4Container
    title Contenedores — Despliegue continuo en el servidor local

    Person_Ext(operador, "Operador", "Consulta estado, recarga inventario")
    System_Ext(github, "GitHub", "Emite webhooks firmados")
    System_Ext(ghcr, "GHCR", "Registro de imagenes")
    System_Ext(cloudflare, "Cloudflare Edge", "TLS y filtro por ruta")

    System_Boundary(host, "Host Linux") {
        Container(cfd, "cloudflared", "Binario Go", "Tunel saliente; unico camino desde Internet")
        Container(receptor, "Receptor", "Python 3.12 / FastAPI", "Valida firma, empareja contra inventario, encola y despliega")
        Container(proxy, "Socket-proxy", "tecnativa/docker-socket-proxy", "API de Docker recortada en loopback")
        Container(docker, "Daemon Docker", "dockerd", "Ejecuta los contenedores de aplicacion")
        Container(apps, "Contenedores de aplicacion", "Imagenes OCI", "Los servicios desplegados")
        ContainerDb(estado, "Estado y logs", "JSON y JSONL en disco", "Tag actual y anterior por app; historico de despliegues")
        ContainerDb(inventario, "Inventario", "apps.yml", "Allowlist de aplicaciones desplegables")
    }

    Rel(github, cloudflare, "Webhook firmado", "HTTPS + HMAC")
    Rel(cloudflare, cfd, "Entrega solo /webhook", "Tunel")
    Rel(cfd, receptor, "Reenvia", "HTTP 127.0.0.1:9000")
    Rel(receptor, inventario, "Lee al arrancar y al recargar", "YAML")
    Rel(receptor, proxy, "Ordena pull y up", "API Docker 127.0.0.1:2375")
    Rel(proxy, docker, "Reenvia lo permitido", "Socket unix :ro")
    Rel(docker, ghcr, "Descarga imagenes", "OCI/HTTPS")
    Rel(docker, apps, "Crea y arranca", "runtime")
    Rel(receptor, apps, "Healthcheck", "HTTP")
    Rel(receptor, estado, "Persiste resultado", "Escritura atomica")
    Rel(operador, receptor, "Consulta y recarga", "HTTP loopback")

    UpdateElementStyle(receptor, $bgColor="#1168bd", $fontColor="#ffffff", $borderColor="#0b4884")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

*Eje estructura — fase 02-design. El socket-proxy es el único que toca el daemon; el receptor
nunca ve `/var/run/docker.sock`.*

## Componentes internos del receptor

```mermaid
C4Component
    title Componentes — Receptor de webhooks

    Container_Ext(cfd, "cloudflared", "Tunel", "Entrega la peticion")
    ContainerDb_Ext(estado, "Estado en disco", "JSON", "Tag actual y anterior")
    Container_Ext(proxy, "Socket-proxy", "API Docker", "Superficie recortada")

    Container_Boundary(receptor, "Receptor") {
        Component(api, "main", "FastAPI", "Endpoints webhook, health, status y reload")
        Component(seguridad, "security", "hmac + OrderedDict", "Verifica la firma y deduplica entregas")
        Component(eventos, "events", "Funciones puras", "Traduce el payload a una intencion de despliegue")
        Component(config, "config", "dataclasses + PyYAML", "Ajustes del proceso e inventario de apps")
        Component(cola, "queue", "asyncio", "Un worker por aplicacion; serializa despliegues")
        Component(deployer, "deployer", "asyncio.subprocess + httpx", "Pull, arranque, healthcheck y rollback")
    }

    Rel(cfd, api, "POST /webhook", "HTTP")
    Rel(api, seguridad, "Verifica firma y delivery", "llamada")
    Rel(api, eventos, "Traduce el payload", "llamada")
    Rel(api, config, "Consulta el inventario", "llamada")
    Rel(api, cola, "Encola el trabajo", "llamada")
    Rel(cola, deployer, "Ejecuta el despliegue", "await")
    Rel(deployer, proxy, "docker compose pull y up", "DOCKER_HOST")
    Rel(deployer, estado, "Lee y escribe el tag", "ficheros")

    UpdateElementStyle(seguridad, $bgColor="#c0392b", $fontColor="#ffffff")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

*Eje estructura — fase 02-design. `security` en rojo: es el único componente cuya corrección
sostiene todo el modelo de confianza.*

## Flujo crítico: del webhook a la versión viva

```mermaid
sequenceDiagram
    autonumber
    participant GH as GitHub
    participant CF as Cloudflare
    participant API as main
    participant SEC as security
    participant EV as events
    participant Q as queue
    participant D as deployer
    participant DK as socket-proxy + Docker
    participant APP as Contenedor

    GH->>CF: POST /webhook (workflow_run, firmado)
    CF->>API: reenvia solo /webhook
    API->>API: lee cuerpo crudo (limite 1 MiB)
    API->>SEC: verify_signature(secreto, cuerpo, cabecera)
    alt firma invalida
        SEC-->>API: SignatureError
        API-->>GH: 401 firma invalida
    else firma valida
        API->>SEC: seen_before(delivery_id)
        alt entrega repetida
            API-->>GH: 202 ignored (ya procesado)
        else entrega nueva
            API->>EV: parse_event(evento, payload)
            EV-->>API: DeployIntent(repo, rama, sha)
            API->>API: _match_app contra el inventario
            alt no encaja
                API-->>GH: 202 ignored (con el motivo)
            else encaja
                API->>Q: submit(app, sha, delivery)
                API-->>GH: 202 queued
                Note over API,GH: respuesta en milisegundos,<br/>muy por debajo del timeout de 10 s
                Q->>D: deploy(app, sha)
                D->>DK: compose pull (IMAGE_TAG=sha-1a2b3c4)
                D->>DK: compose up -d --remove-orphans
                DK->>APP: recrea el contenedor
                loop hasta health_timeout
                    D->>APP: GET health_url
                end
                alt healthcheck correcto
                    D->>D: persiste tag actual y anterior
                else healthcheck fallido
                    D->>DK: compose up -d con el tag anterior
                    D->>D: registra fallo y rollback
                end
            end
        end
    end
```

*Eje comportamiento — fase 02-design. La respuesta a GitHub se emite **antes** de desplegar
(ADR-0008): el resultado no viaja de vuelta, y esa es la deuda conocida del diseño.*

## Ciclo de vida de un despliegue

```mermaid
stateDiagram-v2
    [*] --> Recibido
    Recibido --> Rechazado: firma invalida (401)
    Recibido --> Ignorado: repetido, rama, workflow o repo
    Recibido --> Encolado: validado y emparejado

    Encolado --> Desplegando: el worker de la app lo toma
    note right of Encolado
        Un worker por aplicacion:
        la misma app se serializa,
        apps distintas van en paralelo
    end note

    Desplegando --> Verificando: pull y up correctos
    Desplegando --> Fallido: error de pull o de arranque

    Verificando --> Vivo: health_url responde < 400
    Verificando --> Fallido: se agota health_timeout

    Fallido --> Revirtiendo: hay tag anterior y rollback activo
    Fallido --> Detenido: sin tag anterior o rollback desactivado

    Revirtiendo --> Revertido: el tag anterior vuelve a levantar
    Revirtiendo --> Detenido: el rollback tambien falla

    Vivo --> [*]: tag persistido como actual
    Revertido --> [*]: registrado en el JSONL
    Detenido --> [*]: requiere intervencion manual
    Rechazado --> [*]
    Ignorado --> [*]
```

*Eje comportamiento — fase 02-design. `Detenido` es el único estado que exige a una persona.*

## Modelo de datos del dominio

```mermaid
classDiagram
    class AppConfig {
        +str name
        +str repo
        +Path project_dir
        +str image
        +str branch
        +str event
        +str workflow
        +str tag_template
        +str health_url
        +int health_timeout
        +bool rollback
        +tag_for(sha) str
        +compose_path() Path
    }
    class DeployIntent {
        +str repo
        +str branch
        +str sha
        +str event
        +str workflow
    }
    class Job {
        +AppConfig app
        +str sha
        +str delivery_id
        +str queued_at
    }
    class DeployResult {
        +str app
        +str sha
        +str tag
        +bool ok
        +bool rolled_back
        +float seconds
        +str error
        +as_dict() dict
    }
    class Step {
        +str name
        +bool ok
        +float seconds
        +str detail
    }
    class Settings {
        +str webhook_secret
        +Path apps_file
        +str host
        +int port
        +str docker_host
        +int max_body_bytes
    }

    DeployIntent ..> AppConfig : se empareja con
    Job *-- AppConfig
    DeployResult *-- Step
    Job ..> DeployResult : produce
    Settings ..> AppConfig : localiza el inventario
```

*Eje estructura — fase 02-design. `DeployIntent` es inmutable y desacopla el formato de GitHub
del resto: si GitHub cambia el payload, solo `events.py` se toca.*

## Decisiones que sostienen esta arquitectura

| Decisión | ADR |
|---|---|
| `workflow_run` como disparador, no `push` | [ADR-0002](../00-project/adr/0002-disparador-workflow-run.md) |
| Tag inmutable por SHA y rollback al anterior | [ADR-0003](../00-project/adr/0003-tag-inmutable-por-sha.md) |
| Túnel de Cloudflare como único ingress | [ADR-0004](../00-project/adr/0004-cloudflare-tunnel-unico-ingress.md) |
| Socket-proxy en lugar del grupo `docker` | [ADR-0005](../00-project/adr/0005-socket-proxy-en-lugar-de-grupo-docker.md) |
| Placement: on-prem, GHCR y GitHub Actions | [ADR-0006](../00-project/adr/0006-placement-mecanismo-cd-y-registro.md) |
| El inventario es la allowlist, fuera del repo | [ADR-0007](../00-project/adr/0007-inventario-como-allowlist.md) |
| Encolar y responder `202` con motivo | [ADR-0008](../00-project/adr/0008-cola-por-app-y-respuesta-202.md) |

## Límites conocidos de la arquitectura

| Límite | Consecuencia | Salida si deja de ser aceptable |
|---|---|---|
| Un receptor por servidor | No hay despliegue multi-host coordinado | Declarar el webhook en varios destinos, o un orquestador por encima |
| El resultado no vuelve a GitHub | La entrega figura correcta aunque el despliegue falle | Notificaciones desde `queue._record` (Gate 4) |
| Sin despliegue sin cortes | Segundos de indisponibilidad al recrear | Proxy delante con dos réplicas |
| El rollback no revierte datos | Una migración aplicada permanece | Migraciones compatibles hacia atrás en cada app |
| El `.jsonl` no rota | Crecimiento sin límite del log | `logrotate` o rotación en `_append_log` (Gate 4) |
