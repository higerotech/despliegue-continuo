# Threat Model — Receptor de webhooks y cadena de despliegue

* **Estado:** approved
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.5.0
* **Gate:** 1
* **Alcance:** receptor + ingress por túnel + socket-proxy + cadena de suministro de imágenes + estado en disco
* **Metodología:** STRIDE + DREAD
* **Clasificación de datos (ref):** `docs/00-project/data-classification.md`

## Diagrama de flujo de datos (DFD)

```mermaid
flowchart LR
    subgraph INET [Trust boundary 1: Internet]
      GH([GitHub])
      ATK([Atacante])
      DEV([Colaborador del repo])
    end
    subgraph CF [Trust boundary 2: Cloudflare Edge]
      EDGE{{Filtro por ruta: solo /webhook}}
    end
    subgraph HOST [Trust boundary 3: Host Linux]
      CFD[cloudflared]
      RX["Receptor 127.0.0.1:9000<br/>usuario deploy, sin grupo docker"]
      INV[(apps.yml 0640)]
      SEC[(receiver.env 0600 root)]
      ST[(Estado y logs)]
      subgraph DKR [Trust boundary 4: superficie Docker]
        PX[Socket-proxy 2375]
        DD[(dockerd - root)]
        APPS[Contenedores de aplicacion]
      end
      CFD --> RX
      RX --> PX
      PX --> DD
      DD --> APPS
      RX --> ST
      RX --> INV
      RX --> SEC
      RX -->|healthcheck| APPS
    end
    subgraph SUPPLY [Trust boundary 5: cadena de suministro]
      GHCR([GHCR])
      PYPI([PyPI y Docker Hub])
    end
    GH -->|webhook firmado HMAC| EDGE
    DEV -->|push y pull request| GH
    ATK -.->|sin firma| EDGE
    ATK -.->|escaneo directo al host| HOST
    EDGE --> CFD
    GHCR -.->|pull de imagenes| DD
    PYPI -.->|dependencias en instalacion| RX
```

*Eje comportamiento — fase 02-design. Insumo del STRIDE. La frontera 4 es la crítica: cruzarla
es alcanzar un daemon que corre como root.*

## Análisis STRIDE

| Componente / flujo | Spoofing | Tampering | Repudiation | Info Disclosure | DoS | Elevation |
|---|---|---|---|---|---|---|
| **Entrada del webhook** (Edge → receptor) | **T1**: petición falsificada haciéndose pasar por GitHub | **T2**: payload manipulado para inyectar rutas o comandos | **T11**: replay de una entrega legítima sin trazar | — | **T12**: cuerpo enorme o avalancha de peticiones | — |
| **Receptor → socket-proxy** | — | — | Acciones sobre Docker sin atribución a una entrega | — | — | **T4**: crear contenedor privilegiado ⇒ root del host |
| **Emparejamiento con el inventario** | **T5**: desplegar un repositorio no declarado | Manipular `apps.yml` si se tiene acceso al host | — | Rutas internas y endpoints de salud | — | — |
| **Endpoints de administración** | — | **T7**: `/reload` cambiaría el inventario | — | **T7**: `/status` revela inventario e histórico | — | — |
| **Secreto y credenciales** | **T10**: quien tiene el secreto **es** GitHub para el sistema | — | — | **T10**: fuga por logs, backup o repositorio | — | T10 ⇒ despliegue arbitrario |
| **Túnel** | **T8**: con el token del túnel se publica otro origen | — | — | — | Caída ⇒ sin despliegues | — |
| **Cadena de suministro** (GHCR, PyPI) | Imagen suplantada en el registro | **T6**: desplegar una build no verificada | — | — | — | Dependencia comprometida ⇒ T4 |
| **Puertos del host** | — | — | — | — | **T3**: escaneo y ataque directo | — |
| **Cola de despliegues** | — | **T9**: despliegues solapados dejan estado inconsistente | — | — | — | — |

## Priorización DREAD

Cada dimensión de 1 a 5; puntuación = media. **≥ 4,0 crítica · 3,0–3,9 alta · 2,0–2,9 media ·
< 2,0 baja.** Valores **tras** aplicar los controles del diseño actual.

| ID | Amenaza | D | R | E | A | Di | Score | Nivel | Control (trazable) |
|---|---|---|---|---|---|---|---|---|---|
| **T4** | Escalada a root del host desde el receptor | 5 | 3 | 2 | 5 | 3 | **3,6** | Alta | ADR-0005: socket-proxy; `exec`/`secrets`/`system` cerrados. **No eliminada** |
| **T10** | Fuga del secreto HMAC | 5 | 4 | 2 | 4 | 2 | **3,4** | Alta | `0600 root:root`, fuera del repo, ≥ 32 caracteres forzados en `config.load_settings` |
| **T1** | Petición falsificada | 5 | 1 | 1 | 4 | 5 | **3,2** | Alta | RS01: HMAC-SHA256 con `compare_digest`, validado **antes** de parsear el JSON |
| **T6** | Despliegue de build no verificada | 3 | 4 | 3 | 3 | 3 | **3,2** | Alta | ADR-0002: `workflow_run` + `conclusion: success` + nombre del workflow |
| **T8** | Robo del token del túnel | 4 | 2 | 2 | 4 | 2 | **2,8** | Media | `0600` fuera del repo; rotación documentada en el runbook |
| **T2** | Inyección por payload | 5 | 1 | 1 | 4 | 3 | **2,8** | Media | ADR-0007: `create_subprocess_exec` sin shell; ninguna ruta sale del payload |
| **T5** | Despliegue de repo no declarado | 3 | 2 | 1 | 3 | 4 | **2,6** | Media | ADR-0007: el inventario es la allowlist; emparejamiento en 4 dimensiones |
| **T9** | Despliegues solapados | 2 | 3 | 2 | 3 | 3 | **2,6** | Media | ADR-0008: un worker por app; escritura atómica del estado |
| **T7** | Exposición de `/status` y `/reload` | 3 | 2 | 1 | 2 | 4 | **2,4** | Media | Filtro `^/webhook$` en el túnel **más** guardia loopback/token en el receptor |
| **T12** | Agotamiento de recursos | 2 | 4 | 3 | 2 | 1 | **2,4** | Media | Límite de 1 MiB antes de parsear; `command_timeout`; cola acotada |
| **T11** | Replay de una entrega | 2 | 3 | 2 | 2 | 2 | **2,2** | Media | Caché LRU de 1024 `X-GitHub-Delivery`; además el redespliegue es idempotente |
| **T3** | Ataque directo a puertos | 4 | 1 | 1 | 4 | 1 | **2,2** | Media | ADR-0004: nada escucha en interfaz pública; `BIND_HOST=127.0.0.1` probado |

```mermaid
quadrantChart
    title Riesgo residual tras los controles del diseno
    x-axis "Probabilidad baja" --> "Probabilidad alta"
    y-axis "Impacto bajo" --> "Impacto alto"
    quadrant-1 "Mitigar ya"
    quadrant-2 "Vigilar"
    quadrant-3 "Aceptar"
    quadrant-4 "Mitigar si escala"
    "T4 escalada a root": [0.20, 0.95]
    "T10 fuga del secreto": [0.22, 0.90]
    "T1 peticion falsificada": [0.08, 0.88]
    "T6 build no verificada": [0.18, 0.60]
    "T8 robo del token": [0.15, 0.72]
    "T2 inyeccion por payload": [0.06, 0.85]
    "T5 repo no declarado": [0.10, 0.58]
    "T9 despliegues solapados": [0.12, 0.35]
    "T7 exposicion de admin": [0.09, 0.45]
    "T12 agotamiento": [0.25, 0.28]
    "T11 replay": [0.15, 0.20]
    "T3 ataque a puertos": [0.05, 0.72]
```

*Eje trazabilidad — fase 02-design. Comparar con el cuadrante del PRD: los controles desplazan
casi todo hacia la izquierda (menos probable), pero **el impacto de T4 y T10 no baja**, porque
ninguno de los dos controles reduce lo que ocurre si se materializan.*

## La amenaza que no se cierra: T4

Merece su propia sección porque es la única cuyo control es **parcial y conocido como tal**.

Desplegar contenedores exige `POST /containers/create`. Con ese permiso —lo tenga el grupo
`docker` o un socket-proxy— se puede crear un contenedor privilegiado montando `/`. Por tanto:

- El socket-proxy **reduce la probabilidad** (bloquea `exec`, `secrets`, `swarm`, `system`,
  `build`) y añade un punto único de auditoría. En la tabla eso mueve `E` (explotabilidad) de 4
  a 2 y `R` de 4 a 3.
- El socket-proxy **no reduce el impacto**: `D` y `A` siguen en 5. Quien comprometa el receptor
  sigue pudiendo comprometer el host.

**Docker rootless es la única mitigación que cambia el impacto.** Está registrada como
evolución, no como control vigente. Documentarla como implementada sería falsear el modelo.

Condición de escalado a requisito, ya fijada en ADR-0005: si el receptor pasara a desplegar
aplicaciones de terceros, o apareciera una segunda superficie de entrada.

## Controles verificados

| Control | Evidencia |
|---|---|
| Firma HMAC validada antes de parsear | `test_security.py` (6 casos de rechazo) + `test_webhook.py::test_rechaza_una_firma_de_otro_secreto` |
| Reentregas deduplicadas | `test_security.py::TestDeliveryCache` (4 casos) + `test_webhook.py::test_la_reentrega_...` |
| Emparejamiento estricto | `test_webhook.py::test_ignora_lo_que_no_esta_declarado` (rama, workflow, repo) |
| Secreto débil rechazado al arrancar | `test_config.py::test_falla_con_un_secreto_corto` |
| Escucha solo en loopback | `test_config.py::test_escucha_en_loopback_por_defecto` |
| Nunca el socket crudo de Docker | `test_config.py::test_apunta_al_socket_proxy_por_defecto` |
| `exec` y `system` bloqueados en el proxy | Verificación manual contra Docker 29.5.2: ambos devuelven `403` |
| Nada sensible en el repositorio | `git ls-files` no lista `.env` ni `config/apps.yml` |

## Deuda de seguridad reconocida

| ID | Deuda | Riesgo que deja abierto | Destino |
|---|---|---|---|
| **DS-01** | Docker rootless no implementado | T4 conserva impacto total | Evolución; requisito si cambian las condiciones de ADR-0005 |
| **DS-02** | Sin rotación del `.jsonl` de despliegues | Disco lleno ⇒ el receptor deja de registrar | Gate 4 |
| **DS-03** | Sin notificación del resultado del despliegue | Un fallo puede pasar inadvertido (A09) | Gate 4 |
| **DS-04** | Sin procedimiento probado de rotación del secreto | T10 tarda más en cerrarse tras una sospecha | Runbook, Gate 4 |
| ~~**DS-05**~~ | ~~Sin SBOM ni escaneo de dependencias~~ | — | **Cerrada**: `pip-audit` y SBOM CycloneDX en CI (job `sca`) |
| **DS-06** | `health_url` es opcional | Sin ella no hay verificación real ni rollback fiable | Decisión pendiente: hacerla obligatoria en `load_apps` |
