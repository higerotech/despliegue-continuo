# ADR-0002: `workflow_run` como disparador, no `push`

* **Estado:** accepted
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0002
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A08 (integridad de software y datos), A06 (diseño inseguro)

## Contexto

Origina: RF01 y RF02 del PRD. Un webhook de GitHub puede disparar el despliegue con varios
eventos. El más obvio es `push`, pero tiene dos defectos graves en este diseño:

1. **Llega antes de que exista la imagen.** El build corre en GitHub Actions después del push;
   un despliegue disparado por `push` intentaría hacer `pull` de un tag que aún no está en
   GHCR y fallaría por una carrera, no por un error real.
2. **No sabe si el código funciona.** `push` se emite igual con los tests en rojo. Desplegar
   ahí es desplegar código no verificado por construcción.

## Decisión

El disparador por defecto es **`workflow_run` con `action: completed` y `conclusion: success`**,
filtrado además por el **nombre del workflow** declarado en el inventario.

El triple filtro es necesario porque GitHub emite `workflow_run` para *todos* los workflows del
repositorio: sin el filtro por nombre, el workflow `ci` de las pruebas dispararía un despliegue
igual que el de build.

`push` sigue soportado (`event: push` en `apps.yml`) para aplicaciones que se construyen en el
propio servidor y no dependen de un registro externo, pero no es el modo por defecto.

Implementación: `events._parse_workflow_run` y el emparejamiento estricto en
`main._match_app`.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo |
|---|---|---|---|
| **`workflow_run` + filtro por nombre (elegida)** | Solo despliega lo construido y verificado; sin carreras | Requiere que el nombre del workflow coincida con el inventario | Un workflow renombrado deja de desplegar — falla en seguro |
| `push` | Simple, inmediato | Carrera con el build; despliega con tests en rojo | Despliegue de código roto |
| Evento `package` (publicación en GHCR) | Garantiza que la imagen existe | No dice si los tests pasaron; payload menos estable | Despliegue de imagen construida desde código fallido |
| `curl` al receptor desde un step del workflow | Control total del momento | Duplica la lógica de firma en cada repositorio de aplicación | Secreto del webhook replicado en N repos |

## Consecuencias

- Positivas: RF02 se cumple por construcción. Un build en rojo produce
  `202 ignored: workflow_run conclusion='failure'`, visible en *Recent Deliveries* de GitHub.
- Negativas / deuda asumida: acoplamiento por **nombre** entre el workflow de la aplicación y
  su entrada en `apps.yml`. Renombrar el workflow silencia los despliegues sin error evidente;
  el diagnóstico está documentado en el runbook y la respuesta `202` dice exactamente eso.
- Impacto en threat model: mitiga **T6** (despliegue de una build rota). No introduce amenazas:
  el evento sigue llegando firmado y validado antes de parsearse.
