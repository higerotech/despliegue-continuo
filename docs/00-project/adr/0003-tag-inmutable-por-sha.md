# ADR-0003: Tag inmutable derivado del SHA, con rollback al tag anterior

* **Estado:** accepted
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0003
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A08 (integridad de software y datos), A09 (fallos de registro y monitorización)

## Contexto

Origina: RF03, RF05 y RNF02. El `docker-compose.yml` de cada aplicación debe referenciar una
imagen. La opción cómoda es `:latest` y un `docker compose pull` periódico, pero deja el
sistema sin respuestas a dos preguntas operativas básicas: *¿qué código está corriendo ahora
mismo?* y *¿a dónde vuelvo si esto se rompe?*

Con `:latest`, el contenido del tag cambia bajo los pies: dos hosts que hicieron `pull` en
momentos distintos corren código distinto con la misma etiqueta, y un rollback no tiene destino
al que apuntar.

## Decisión

El receptor inyecta `IMAGE_TAG=sha-<7 primeros del SHA>` en el entorno de `docker compose`. El
compose de la aplicación **debe** usar `${IMAGE_TAG}` y no puede fijar un tag literal — la
plantilla usa `${IMAGE_TAG:?...}` para que falle ruidosamente si falta.

El tag lo produce `docker/metadata-action` con `type=sha`, cuyo formato por defecto
(`sha-` + 7 caracteres) coincide exactamente con el `tag_template` por defecto del receptor.
`latest` se sigue publicando, pero **solo como alias informativo**: nadie despliega por él.

Tras cada despliegue con healthcheck correcto, el receptor persiste en
`/var/lib/cd-receiver/<app>.json` el tag actual y el anterior. El **rollback consiste en volver
a levantar el tag anterior**, que sigue existiendo en GHCR porque los tags por SHA no se
sobrescriben nunca.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo |
|---|---|---|---|
| **Tag por SHA + estado en disco (elegida)** | Reproducible; `docker ps` dice qué commit corre; rollback trivial | Acumula tags en GHCR; requiere estado local | Pérdida del fichero de estado ⇒ primer rollback sin destino |
| `:latest` + `pull` | Cero configuración | No se sabe qué corre; sin rollback; despliegues no reproducibles | Alto: imposible auditar un incidente |
| Digest `@sha256:...` | Inmutabilidad criptográfica, no solo convencional | Ilegible en logs y en `docker ps`; el operador no puede teclearlo | Fricción operativa alta |
| Versión semántica por tag de git | Legible, alineada con releases | Obliga a etiquetar para cada despliegue; no todo commit desplegable es una release | Fricción en el flujo diario |

El **digest** es estrictamente más seguro que el tag por SHA: un tag es una convención mutable
del lado del registro y el digest no. Se descartó por legibilidad operativa; queda registrado
como evolución posible si el registro dejara de ser de confianza.

## Consecuencias

- Positivas: RF05 (rollback) se implementa en `deployer._rollback` sin lógica adicional; RNF02
  (reproducibilidad) es una propiedad del diseño. Un rollback manual es una línea:
  `IMAGE_TAG=sha-1a2b3c4 docker compose up -d`.
- Negativas / deuda asumida: GHCR acumula un tag por commit desplegado; hará falta una política
  de retención de imágenes (anotada para Gate 4). Si se pierde `/var/lib/cd-receiver/`, el
  primer rollback posterior no tiene destino y el despliegue simplemente queda fallido — el
  código lo maneja (`previous_tag` a `None`) sin romperse.
- Impacto en threat model: mitiga **T9** (despliegue de una build rota que deja el servicio
  caído). Introduce dependencia de la integridad del fichero de estado, escrito de forma atómica
  (`tmp.replace(path)`) para que un corte no lo deje a medias.
