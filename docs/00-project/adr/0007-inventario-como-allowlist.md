# ADR-0007: El inventario `apps.yml` es la allowlist, y vive fuera del repositorio

* **Estado:** accepted
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0007
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A01 (control de acceso), A05 (inyección), A06 (diseño inseguro)

## Contexto

Origina: RS03 y RF04. El receptor recibe un payload de GitHub y tiene que decidir **qué**
desplegar y **dónde**. La tentación de diseño es tomar esos datos del propio payload: el repo
dice su nombre, el evento trae la rama y el SHA, y el directorio de trabajo podría derivarse
del nombre del repositorio.

Eso convertiría un mensaje externo en una ruta del sistema de ficheros y en el nombre de una
imagen a descargar y ejecutar. Con la firma HMAC válida el riesgo es acotado, pero el patrón es
frágil: cualquier fallo en la validación se traduce directamente en ejecución.

## Decisión

**Del payload solo se usan tres valores, y ninguno es una ruta:** `repository.full_name` (para
buscar en el índice), la rama y el SHA (para construir el tag). Todo lo demás —directorio del
proyecto, fichero compose, imagen, URL de salud, tiempos— sale de `apps.yml`.

El inventario es simultáneamente **la allowlist**: `main._match_app` busca el repo en el índice
y, si no está, responde `202 ignored` sin desplegar. No hay ruta por la que un repositorio no
declarado llegue a ejecutar nada.

El emparejamiento es **estricto en cuatro dimensiones**: repo, rama, tipo de evento y nombre
del workflow. Cualquier discrepancia detiene el despliegue con un motivo explícito.

Refuerzos de implementación:

- El repo se normaliza a minúsculas al cargar el inventario y al parsear el evento, porque
  GitHub no distingue mayúsculas en `full_name`
  (`tests/test_config.py::test_normaliza_el_repo_a_minusculas`).
- Los comandos se lanzan con `asyncio.create_subprocess_exec` y **lista de argumentos, sin
  shell**: no hay interpretación de metacaracteres en ningún punto.
- `apps.yml` **no se versiona** (`.gitignore`). Es específico de la instalación y, en un
  repositorio público, publicar rutas internas y endpoints de salud no aporta nada.
- Se recarga en caliente con `POST /reload` sin reiniciar el servicio ni cortar despliegues en
  curso.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo |
|---|---|---|---|
| **Inventario declarativo como allowlist (elegida)** | Superficie mínima; el payload no controla rutas; auditable de un vistazo | Hay que declarar cada app a mano | Un despliegue no declarado falla en seguro |
| Derivar el directorio del nombre del repo | Cero configuración | Convierte una cadena externa en una ruta; *path traversal* si la validación falla | Alto |
| Descubrimiento automático de `/srv/apps/*` | Añadir apps sin tocar configuración | Cualquier directorio que aparezca pasa a ser desplegable | Alto |
| Inventario versionado en el repositorio | Historial de cambios; revisión por PR | Expone rutas internas en repositorio público; obliga a desplegar el receptor para añadir una app | Medio |

La última merece matiz: versionar el inventario tendría valor real de auditoría. Se descartó
por la visibilidad pública del repositorio. Si el repositorio pasara a privado, es la primera
decisión a reconsiderar.

## Consecuencias

- Positivas: RS03 se cumple por construcción. Cubierto por
  `tests/test_webhook.py::test_ignora_lo_que_no_esta_declarado`, que verifica las tres formas de
  discrepancia (rama, workflow y repo). El diagnóstico es inmediato: la respuesta `202` dice
  cuál de los cuatro criterios falló y GitHub la muestra en *Recent Deliveries*.
- Negativas / deuda asumida: añadir una aplicación requiere editar un fichero en el servidor;
  no hay revisión por PR de ese cambio ni historial de quién lo tocó. Para el tamaño actual del
  parque es aceptable; con más operadores dejaría de serlo.
- Impacto en threat model: mitiga **T5** (despliegue de un repositorio ajeno) y **T2**
  (inyección de comandos vía payload).
