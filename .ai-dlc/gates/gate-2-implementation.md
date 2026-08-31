# Gate 2 — Implementación (cierre de Fase 03)

**Estado: NO SUPERADO — 2026-08-30** · Fase 03 documentada; el gate queda **abierto**.

La documentación de la fase 03 está completa (`deployment-runbook.md`, `repo-history.md`), pero
**tres de los cinco criterios exigen herramientas que aún no se han ejecutado**. Marcar este
gate como superado sería falsear el control.

- [ ] **SAST sin findings críticos/altos**
      → **No ejecutado.** No hay análisis estático en el workflow `ci`.
        *Para cerrarlo:* añadir `bandit` (o `ruff` con reglas de seguridad) a `ci.yml`.
- [ ] **Dependencias verificadas (SCA) — sin deps alucinadas ni vulnerables (A03)**
      → **No ejecutado.** Las 4 dependencias directas están fijadas a versiones publicadas y
        verificadas contra PyPI (`pip index versions`), pero no hay escaneo de CVE ni SBOM.
        Corresponde a las deudas **DS-05** y **D-04**.
        *Para cerrarlo:* `pip-audit` y generación de SBOM en `ci.yml`.
- [ ] **Cobertura ≥ 80 % branch**
      → **No medida.** 52 pruebas en verde, pero sin `pytest --cov`. Se sabe que
        `deployer.py` (220 líneas) no tiene pruebas unitarias: la cobertura de rama real está
        casi con seguridad por debajo del umbral. Deudas **D-02** y **D-05**.
        *Para cerrarlo:* medir, y cubrir `deployer.py`.
- [ ] **Dual review completado (humano + IA)**
      → **Pendiente de la mitad humana.** El código se escribió y revisó con asistencia de IA;
        falta la revisión de Jeremi Alcala.
- [x] **Sin secretos en el código**
      → Verificado: `git ls-files` no lista `.env` ni `config/apps.yml`; `.gitignore` los
        excluye; el instalador genera el secreto en el servidor y no lo persiste en el
        repositorio. `config.load_settings` rechaza arrancar con un secreto de menos de 32
        caracteres.

## Qué falta, en orden de valor

1. `pip-audit` en CI — cierra DS-05 y es el más barato de los tres.
2. Medición de cobertura y pruebas de `deployer.py` — cierra D-02 y D-05.
3. SAST — el de menor valor marginal aquí: la superficie es pequeña y ya revisada, pero es
   criterio del gate.
4. Revisión humana del código.
