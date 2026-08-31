# ADR-0001: Adopción retroactiva de la estructura AI-DLC

* **Estado:** accepted
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 1.0.0
* **ID:** ADR-0001
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** — (decisión de proceso, no de producto)

## Contexto

El receptor de webhooks se implementó y verificó **antes** de aplicar AI-DLC: existían el
código, 52 pruebas verdes y un `docs/ARCHITECTURE.md` con las decisiones razonadas, pero no
había artefactos de fase, threat model formal ni gates.

Esto invierte el orden habitual de la metodología, que documenta antes de construir. La
pregunta no era si adoptar AI-DLC, sino cómo hacerlo sin fingir una historia que no ocurrió.

## Decisión

Se adopta la estructura AI-DLC de forma **retroactiva y declarada como tal**, con tres reglas:

1. **La documentación describe lo que el código hace, no lo que idealmente haría.** Cada
   requisito y cada control se contrasta contra el código o una prueba concreta; lo que no
   existe se marca como deuda, no como implementado.
2. **Los Gates 0 a 3 se cierran en la misma fecha**, porque la evidencia de los cuatro ya
   existía al escribir los documentos. El `CHANGELOG.md` lo explica en la entrada `0.4.0` en
   lugar de simular cuatro cortes sucesivos.
3. **Registro único de ADRs en `docs/00-project/adr/`**, siguiendo la convención de
   `signoz-deployment` e `instalador-docker-compose`, aunque la guía de deployment placement
   proponga `docs/02-design/adr/`. Fragmentar la numeración sería peor que la desviación.

`docs/ARCHITECTURE.md` se conserva como resumen de ingeniería para quien llega al repositorio;
las decisiones que contenía pasan a ser ADRs numeradas (0002 a 0008) y son la fuente de verdad.

## Alternativas consideradas

| Opción | Pros | Contras |
|---|---|---|
| **Adopción retroactiva declarada (elegida)** | La documentación es verificable contra el código; la trazabilidad es honesta | Los gates pierden su función de control previo en este primer ciclo |
| Documentar como si hubiera precedido al código | Historia "limpia" de gates sucesivos | Falsifica el registro; destruye el valor de auditoría de los gates |
| No adoptar AI-DLC | Cero esfuerzo | Sin threat model formal ni trazabilidad; incoherente con el resto de repositorios |
| Reescribir el sistema siguiendo las fases | Ortodoxo | Tirar código verificado para satisfacer un proceso es coste sin beneficio |

## Consecuencias

- Positivas: el threat model formal ya encontró trabajo pendiente que el diseño informal había
  dejado implícito (rotación del secreto, rotación de logs). La estructura queda alineada con
  los demás repositorios de Higerotech.
- Negativas / deuda asumida: en este ciclo los gates documentan en vez de controlar. A partir
  del Gate 4 recuperan su función: nada nuevo entra sin pasar por su fase.
- Impacto en threat model: ninguno directo; habilita su existencia.
