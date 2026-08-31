# Gate 0 — Requisitos (cierre de Fase 01)

**Estado: SUPERADO — 2026-08-30** · Versión cortada: `0.1.0`

No avanzar a diseño hasta cumplir TODO:

- [x] Requisitos funcionales documentados en `docs/01-requirements/`
      → 8 RF, 4 RNF y 6 RS en `despliegue-continuo-webhook.md`, cada uno con su verificación.
- [x] Requisitos de seguridad mapeados a OWASP ASVS
      → RS01–RS06 con capítulos ASVS en la cabecera y mapeo a OWASP Top 10:2025 por requisito.
- [x] Escenarios **negativos / de abuso** definidos
      → 9 escenarios (AB-01 a AB-09). AB-09 documenta de forma explícita el que solo está
        mitigado parcialmente.
- [x] Threat assessment inicial realizado
      → DFD inicial + `quadrantChart` DREAD con 12 amenazas, previo a controles.
- [x] Datos clasificados (`docs/00-project/data-classification.md`)
      → Inventario de 9 datos en tres niveles, sin datos personales; tres reglas derivadas de
        que el repositorio sea público.
- [x] Charter y glosario aprobados
      → `charter.md` con alcance, no-scope, métricas y riesgos; `glossary.md` con los tres
        contextos acotados y las divergencias con el vocabulario de GitHub.

**Evidencia de los tres ejes:** `journey` + `requirementDiagram` (trazabilidad) · `C4Context`
(estructura) · `DFD` + `quadrantChart` (comportamiento y priorización).
