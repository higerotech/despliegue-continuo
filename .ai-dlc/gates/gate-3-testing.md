# Gate 3 — Pruebas (cierre de Fase 04)

**Estado: NO SUPERADO — 2026-08-30** · Fase 04 documentada; el gate queda **abierto**.

`docs/04-testing/test-strategy.md` está completo y contrastado contra la suite real (52 pruebas
en verde), pero el checklist de este gate pide una batería que un servicio de esta superficie
no tiene montada. Se deja visible en lugar de marcarla.

- [ ] **Pirámide completa pasando (unit → integration → contract → e2e → security)**
      → **Parcial.** Unitarias (43) e integración del endpoint (9) pasan. **No hay pruebas de
        contrato ni e2e automáticas**; el nivel "security" existe como propiedades verificadas
        dentro de las unitarias (14 casos de firma y deduplicación), no como suite separada.
        La transición crítica **rollback** solo está verificada a mano — deuda **D-01**.
- [ ] **Matriz OWASP Top 10 ejecutada**
      → **No ejecutada como matriz.** Cada requisito de seguridad está mapeado a su categoría
        de OWASP Top 10:2025 en el PRD y en las ADRs, pero no hay una ejecución sistemática
        categoría por categoría.
- [ ] **DAST limpio**
      → **No ejecutado.** No se ha pasado ningún escáner dinámico contra el endpoint.
        *Matiz honesto:* la superficie HTTP es de 4 endpoints, uno solo publicado, sin sesiones,
        sin base de datos y sin renderizado; el valor esperado de un DAST aquí es bajo. Aun así,
        el criterio no está cumplido.
- [ ] **Rendimiento dentro de SLOs**
      → **Parcial.** RNF03 (responder antes del timeout de 10 s de GitHub) se cumple por diseño
        y se observó en milisegundos durante las pruebas de humo, pero **no hay prueba de
        rendimiento ni de carga** — deuda **D-03**.
- [ ] **Mutation testing ≥ 60 % (objetivo)**
      → **No ejecutado.** Sin `mutmut` ni equivalente.

## Lo que sí está demostrado

Aunque el gate no se supere, conviene no infravalorar la evidencia existente:

| Verificado | Cómo |
|---|---|
| Rechazo de firmas inválidas, ausentes y malformadas | 6 casos automáticos + prueba en ejecución real (`401`) |
| Deduplicación de reentregas | 4 casos automáticos + prueba en ejecución real |
| Emparejamiento estricto (rama, workflow, repo) | 3 casos automáticos + prueba en ejecución real |
| Rechazo de builds fallidas | 4 casos automáticos |
| Superficie del socket-proxy | Verificación manual contra Docker 29.5.2: `exec` y `system` dan `403` |
| Ciclo `pull` → `up` → healthcheck | Verificación manual con `traefik/whoami`: `HTTP 200` |
| Ruta de rollback (recreado con otro tag) | Verificación manual: `docker inspect` confirma la imagen anterior |

## Qué falta, en orden de valor

1. **D-01: prueba e2e del rollback.** Es la única deuda que protege un requisito crítico
   (RF05). Todo lo demás de esta lista vale menos que esto.
2. D-03: prueba de concurrencia que demuestre la serialización por aplicación.
3. Matriz OWASP ejecutada de forma sistemática.
4. DAST y mutation testing — los de menor valor marginal para esta superficie.
