# Gate 3 — Pruebas (cierre de Fase 04)

**Estado: NO SUPERADO — 2026-08-30** · Fase 04 documentada; el gate sigue **abierto**, pero la
deuda crítica (D-01) está cerrada.

`docs/04-testing/test-strategy.md` está completo y contrastado contra la suite real (52 pruebas
en verde), pero el checklist de este gate pide una batería que un servicio de esta superficie
no tiene montada. Se deja visible en lugar de marcarla.

- [ ] **Pirámide completa pasando (unit → integration → contract → e2e → security)**
      → **Parcial, pero ya no en el punto crítico.** Unitarias (44), integración del endpoint (9)
        y **6 de extremo a extremo contra Docker real** (`test_rollback_e2e.py`) pasan: 59 en
        total. La deuda **D-01 queda cerrada** — el rollback (RF05) tiene prueba automática,
        validada además con una mutación dirigida que la hace fallar.
        Sigue faltando el nivel de **contrato** (nada valida el OpenAPI contra la
        implementación), y "security" existe como propiedades dentro de las unitarias
        (14 casos de firma y deduplicación), no como suite separada.
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
      → **No sistemático.** Se mutó a mano el camino crítico (desactivar la condición de
        rollback en `deployer.deploy`) y **2 pruebas fallaron**, lo que confirma que la suite
        tiene dientes donde importa. No hay `mutmut` ni porcentaje medido — deuda **D-06**.

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
| **Rollback completo: sano → roto → vuelta a sano** | **Automático**: `test_rollback_e2e.py`, 6 pruebas contra Docker real |
| La suite detecta una regresión del rollback | Mutación dirigida: al desactivar el rollback, 2 pruebas fallan |

## Qué falta, en orden de valor

~~D-01: prueba e2e del rollback~~ — **cerrada**. Era la única deuda que protegía un requisito
crítico; lo que queda es de menor impacto:

1. D-02: pruebas unitarias de `deployer.py` para los bordes que las e2e no tocan (timeouts,
   `health_url` ausente, estado corrupto).
2. D-03: prueba de concurrencia que demuestre la serialización por aplicación.
3. Matriz OWASP ejecutada de forma sistemática.
4. Pruebas de contrato del OpenAPI frente a la implementación.
5. DAST y mutation testing sistemático — los de menor valor marginal para esta superficie.
