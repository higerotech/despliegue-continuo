# Gate 1 — Diseño (cierre de Fase 02)

**Estado: SUPERADO — 2026-08-30** · Versión cortada: `0.2.0`

- [x] Arquitectura C4 validada (`docs/02-design/`)
      → `C4Container` (host completo) y `C4Component` (interior del receptor) en
        `architecture.md`, más `C4Context` en fase 01. Todos los bloques Mermaid validados con
        `validate_mermaid.py`.
- [x] Threat model STRIDE de sistema + servicios con superficie relevante
      → `threat-model.md`: STRIDE sobre 5 fronteras de confianza y 9 componentes/flujos,
        12 amenazas identificadas (T1–T12).
- [x] ADRs registrados para decisiones clave
      → ADR-0001 a ADR-0008, incluida la de **placement** (ADR-0006) con matriz PxD por
        componente y precios verificados con fuente y fecha (caducan 2027-02-28).
- [x] Contratos de API definidos
      → `interfaces-contract.md`: OpenAPI 3.1 de los 4 endpoints, esquema completo de
        `apps.yml` y las 3 obligaciones del repositorio de cada aplicación.
- [x] Patrones de seguridad seleccionados por amenaza priorizada (DREAD)
      → Cada una de las 12 amenazas tiene control trazable a ADR o prueba. **T4 queda como
        riesgo alto aceptado y documentado**: el socket-proxy reduce probabilidad pero no
        impacto, y la ADR-0005 fija la condición bajo la cual rootless pasa de evolución a
        requisito.

**Evidencia de los tres ejes:** `C4Container`/`C4Component`/`classDiagram` (estructura) ·
`sequenceDiagram` + `stateDiagram-v2` (comportamiento) · `DFD` STRIDE + `quadrantChart` DREAD +
`quadrantChart` PxD de placement (trazabilidad).

## Nota para el revisor humano

El Gate 1 se cierra con una amenaza en nivel **alto aceptado** (T4, escalada a root). No es un
descuido del análisis: es la consecuencia inevitable de que desplegar contenedores requiera
`POST /containers/create`. Si esa aceptación no es admisible, la salida está identificada
(Docker rootless) y cuantificada en ADR-0005.
