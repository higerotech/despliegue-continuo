# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y este proyecto se adhiere a [Versionado Semántico](https://semver.org/lang/es/).

Convención de corte por gate: Gate 0 → `0.1.0`, Gate 1 → `0.2.0`, Gate 2 → `0.3.0`,
Gate 3 → `0.4.0`, Gate 4 → `0.5.0`, Gate 5 → `1.0.0`.

> **Nota sobre los cuatro primeros cortes.** El código se implementó y verificó **antes** de
> aplicar AI-DLC (ADR-0001), así que las cuatro versiones se cortan en la misma fecha con
> evidencia que ya existía: no hubo cuatro ciclos sucesivos y simular que los hubo falsearía el
> registro.
>
> **Importante:** una versión cortada marca *documentación de fase entregada*, no
> *gate superado*. **Los Gates 0 y 1 están superados; los Gates 2 y 3 NO.** Sus checklists
> exigen SAST, SCA, cobertura medida, DAST y mutation testing, y nada de eso se ha ejecutado.
> El detalle de lo que falta está en `.ai-dlc/gates/gate-2-implementation.md` y
> `gate-3-testing.md`.

## [Unreleased]

### Añadido

- **Prueba de extremo a extremo del rollback contra Docker real** (`test_rollback_e2e.py`,
  6 pruebas): cierra la deuda **D-01**, que era la única que protegía un requisito crítico.
  RF05 pasa de estar verificado a mano a tener cobertura automática del ciclo completo
  `pull` → `up -d` → healthcheck → vuelta al tag anterior.
  El escenario usa dos tags locales sobre un mismo repositorio de imagen (`traefik/whoami`
  que sirve HTTP, `alpine` que arranca y muere) con `pull_policy: never`, de modo que
  `docker compose pull` no va al registro.
- **Validación por mutación del camino crítico**: al desactivar la condición de rollback en
  `deployer.deploy`, 2 pruebas fallan. Una prueba que pasa solo vale si falla cuando el
  código se rompe.
- Marcador `docker` en `pytest.ini`: las e2e se saltan solas si no hay daemon, así que
  `pytest -m "not docker"` sigue siendo útil en una máquina sin Docker.
- CI dividido en cuatro jobs: `test` (rápidas + umbral de cobertura), `test-e2e` (ciclo contra
  Docker), `sast` y `sca`, para que un fallo trivial no tarde tres minutos en aparecer.
- **32 pruebas rápidas nuevas** que cierran cuatro deudas de una vez:
  - `test_deployer.py` (13): bordes que las e2e no tocan — falta el compose, sin `health_url`,
    `pull` fallido, healthcheck agotado, rollback que también falla, estado corrupto,
    escritura atómica. Cierra **D-02**.
  - `test_queue.py` (9): demuestra que la misma aplicación **se serializa** (RNF04) y que
    aplicaciones distintas avanzan en paralelo; histórico acotado; un deployer que revienta no
    mata al worker. Cierra **D-03**.
  - `test_admin.py` (10): `/status` y `/reload` con y sin autorización, que un inventario roto
    no tumba el vigente, cuerpo excesivo y JSON inválido.
- **Cobertura de rama medida y con umbral**: **93,47 %**, `--cov-fail-under=80` en CI. Se mide
  solo con las pruebas rápidas: una puerta que dependa de tener Docker sería frágil.
  Cierra **D-05**.

### Corregido

- **El teardown de las pruebas e2e no derribaba nada y lo hacía en silencio.** Ejecutaba
  `docker compose down` sin `IMAGE_TAG`, y como el compose de prueba usa `${IMAGE_TAG:?}`,
  compose ni siquiera parseaba el fichero; el código de salida no se comprobaba. Cada
  ejecución dejaba un contenedor vivo reteniendo su red, hasta agotar los rangos del daemon
  (`all predefined address pools have been fully subnetted`) y romper pruebas sin relación.
  Ahora se pasa la variable, se comprueba el resultado, hay una limpieza forzada de respaldo
  y un aviso visible si hizo falta usarla.

### Cambiado

- `deployer._run` solo impone `DOCKER_HOST` **si `docker_host` tiene valor**. Un valor vacío
  es ahora una renuncia explícita que deja el destino al cliente del entorno; lo usan las
  pruebas de integración. En producción el valor por defecto sigue siendo el socket-proxy, y
  una prueba lo fija (`test_un_docker_host_vacio_solo_ocurre_si_se_pide_expresamente`).

### Seguridad

- **SAST con `bandit`** en CI (job `sast`): **0 hallazgos** en 645 líneas, en ninguna severidad.
- **SCA con `pip-audit`** sobre las dependencias de producción (job `sca`): sin
  vulnerabilidades conocidas. Se auditan las de producción y no el entorno de desarrollo,
  porque una CVE en `pytest` no llega nunca al servidor.
- **SBOM CycloneDX 1.6** generado en cada ejecución y archivado como artefacto (90 días). No
  se versiona: cambia con cada actualización de dependencias y solo generaría ruido en el diff.
  Cierra la deuda de seguridad **DS-05**.

### Deuda nueva

- **D-06**: mutation testing sistemático (`mutmut`). La mutación del rollback se hizo a mano.
- **D-07**: pruebas de contrato. Nada valida hoy el OpenAPI de `interfaces-contract.md` frente
  a la implementación real.

**Para cerrar los Gates 2 y 3, que siguen abiertos:**

- **Gate 2**: 4 de 5 criterios cumplidos. Queda **solo la revisión humana del código**, que no
  puede automatizarse. Al hacerla, cortar `0.5.0`.
- **Gate 3**: quedan la matriz OWASP ejecutada de forma sistemática, DAST, pruebas de contrato
  (**D-07**) y mutation testing sistemático (**D-06**).

**Para el Gate 4 (despliegue):**

- **DS-02**: rotación del `.jsonl` de despliegues.
- **DS-03**: notificación del resultado del despliegue a un canal externo — hoy un despliegue
  fallido no se comunica a GitHub ni a ningún sitio.
- **DS-05 / D-04**: `pip-audit` y SBOM del receptor en CI.
- **DS-06**: decidir si `health_url` pasa a ser obligatoria en `config.load_apps`.
- Política de retención de imágenes en GHCR.

## [0.4.0] - 2026-08-30

Documentación de la **fase 04 (pruebas)**. **Gate 3 NO superado**: faltan pruebas de contrato y
e2e, matriz OWASP ejecutada, DAST, prueba de carga y mutation testing.

### Añadido

- `docs/04-testing/test-strategy.md`: reparto de las 52 pruebas, trazabilidad
  requisito → prueba con `requirementDiagram`, cobertura de las transiciones del
  `stateDiagram-v2` de arquitectura, y registro de las verificaciones manuales ejecutadas
  contra Docker 29.5.2.
- Registro explícito de cinco deudas de prueba (D-01 a D-05), con **D-01** (rollback sin prueba
  automática) señalada como la única que protege un requisito crítico.

## [0.3.0] - 2026-08-30

Documentación de la **fase 03 (implementación)**. El receptor queda documentado como operable.
**Gate 2 NO superado**: faltan SAST, SCA/SBOM, medición de cobertura y la revisión humana del
código.

### Añadido

- `docs/03-implementation/deployment-runbook.md`: instalación, alta de aplicaciones,
  diagnóstico por respuesta del webhook, rotación del secreto (DS-04) y verificación de
  invariantes de seguridad.
- `docs/03-implementation/repo-history.md`: grafo y bitácora **derivados del historial real**
  del repositorio con `gitgraph_from_log.py`.

### Corregido

- `config._env_int` leía de `os.environ` en lugar del diccionario `env` recibido por
  `load_settings`, lo que dejaba inerte el punto de inyección para todos los ajustes enteros.
  Lo detectó una prueba nueva (`test_rechaza_un_puerto_no_numerico`).

## [0.2.0] - 2026-08-30

Cierre de **Gate 1 (diseño)**. Threat model formal y las siete decisiones de arquitectura.

### Añadido

- `docs/02-design/architecture.md` con C4 Container, C4 Component, secuencia del flujo crítico,
  ciclo de vida del despliegue y modelo de datos del dominio.
- `docs/02-design/threat-model.md`: STRIDE sobre cinco fronteras de confianza, DREAD de doce
  amenazas y seis deudas de seguridad reconocidas.
- `docs/02-design/interfaces-contract.md`: OpenAPI 3.1 del receptor, esquema de `apps.yml` y
  las tres obligaciones del repositorio de cada aplicación.
- ADR-0002 a ADR-0008, incluida la **ADR-0006 de placement** con matriz PxD por componente y
  precios verificados con fuente y fecha.

### Cambiado

- **El acceso a Docker deja de hacerse por el grupo `docker` y pasa por un socket-proxy con la
  API recortada** (ADR-0005). El usuario `deploy` sale del grupo `docker` y el instalador lo
  retira si lo encuentra. Verificado contra Docker 29.5.2: `pull`, `up -d` y el recreado con
  otro tag funcionan; `exec` y `system` devuelven `403`.
  La ADR documenta de forma explícita que esto **reduce pero no elimina** la equivalencia a
  root, porque `POST /containers/create` sigue siendo necesario para desplegar.
- La numeración OWASP de las ADRs se alineó con **Top 10:2025** (`.ai-dlc/owasp-mapping.md`),
  donde inyección es A05 y diseño inseguro A06.

### Seguridad

- Nuevo ajuste `DOCKER_HOST`, con `tcp://127.0.0.1:2375` por defecto: el receptor **nunca**
  apunta al socket crudo salvo que se le indique de forma explícita.

## [0.1.0] - 2026-08-30

Cierre de **Gate 0 (requisitos)**. Adopción retroactiva de AI-DLC sobre un sistema ya
implementado.

### Añadido

- `docs/00-project/charter.md` con el mapa mental del alcance y las métricas de éxito.
- `docs/00-project/glossary.md`: lenguaje ubicuo de los tres contextos acotados, incluida la
  tabla de divergencias con el vocabulario de GitHub.
- `docs/00-project/data-classification.md`, con las tres reglas que impone que el repositorio
  sea público.
- `docs/01-requirements/despliegue-continuo-webhook.md`: 8 requisitos funcionales, 4 no
  funcionales, 6 de seguridad, **nueve escenarios de abuso** y la evaluación DREAD inicial.
- ADR-0001, que deja constancia de que la adopción es retroactiva y por qué no se simula lo
  contrario.
- Estructura `.ai-dlc/` con los seis gates y las plantillas.

[Unreleased]: https://github.com/higerotech/despliegue-continuo/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/higerotech/despliegue-continuo/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/higerotech/despliegue-continuo/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/higerotech/despliegue-continuo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/higerotech/despliegue-continuo/releases/tag/v0.1.0
