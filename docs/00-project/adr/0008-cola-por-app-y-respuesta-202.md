# ADR-0008: Encolar y responder `202` con el motivo, en vez de desplegar en la petición

* **Estado:** accepted
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0008
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A09 (fallos de registro y monitorización), A06 (diseño inseguro)

## Contexto

Origina: RF06, RNF03 y RS06. GitHub **corta la entrega de un webhook en torno a los 10
segundos** y la marca como fallida si no ha recibido respuesta. Un despliegue —`pull` de una
imagen, recreado del contenedor y healthcheck con hasta 90 s de margen— tarda mucho más que
eso.

Desplegar dentro del ciclo de petición produciría entregas marcadas como fallidas incluso
cuando el despliegue termina bien, y los reintentos automáticos de GitHub lanzarían despliegues
solapados de la misma aplicación.

Hay un segundo problema, de operación más que de arquitectura: cuando un webhook llega bien
pero **no debe** desplegar, ¿cómo se entera el operador de por qué?

## Decisión

**Dos decisiones acopladas por la misma restricción de los 10 segundos.**

### 1. Validar, encolar y responder de inmediato

El endpoint valida firma, deduplica la entrega, empareja contra el inventario y **encola**;
responde `202` en milisegundos. El trabajo real lo hace un **worker por aplicación**
(`queue.DeployQueue`), creado bajo demanda.

Un worker por app —en lugar de uno global— da la propiedad que interesa: los despliegues de una
misma aplicación **se serializan** (dos pushes seguidos se aplican en orden, no se pisan),
mientras que aplicaciones distintas avanzan en paralelo. El apagado espera a que terminen los
despliegues en curso (`DeployQueue.drain`).

### 2. La respuesta `202` lleva el motivo

Cuando un evento legítimo no debe desplegar, se responde `202` con
`{"status": "ignored", "reason": "..."}` explicando cuál de los criterios falló. GitHub guarda
el cuerpo de la respuesta en *Settings → Webhooks → Recent Deliveries*: **el diagnóstico queda
en la UI de GitHub, sin entrar por SSH al servidor**.

Un evento ignorado no es un error: `404` o `400` ensuciarían el panel de entregas de rojo y
harían que GitHub reintentase algo que nunca debe desplegar.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo |
|---|---|---|---|
| **Encolar + `202` con motivo (elegida)** | Respeta el timeout; serializa por app; diagnóstico en la UI de GitHub | El resultado del despliegue no viaja en la respuesta | Un fallo posterior solo se ve en `/status` o en el log |
| Desplegar en la petición | La respuesta lleva el resultado real | Timeout garantizado; reintentos ⇒ despliegues solapados | Alto |
| Cola global (un solo worker) | Aún más simple | Una app lenta bloquea a todas las demás | Medio |
| Responder `4xx` a lo ignorado | Semántica HTTP más literal | GitHub reintenta y marca el webhook como defectuoso | Ruido operativo |
| Publicar el estado con la API de Deployments de GitHub | Estado visible en el repositorio | Requiere token con permisos de escritura en cada repo; más superficie | Medio |

## Consecuencias

- Positivas: RNF03 se cumple —las entregas se responden muy por debajo del límite—. La
  serialización por aplicación evita la clase entera de errores por despliegues concurrentes.
  El runbook aprovecha los motivos: hay una tabla que mapea cada texto de `reason` a su causa.
- Negativas / deuda asumida: **el resultado del despliegue no llega a GitHub.** Un despliegue
  encolado que luego falla se ve en `GET /status`, en `journalctl` o en el `.jsonl`, pero la
  entrega aparece como correcta en la UI. Es la deuda más visible del diseño y el candidato
  natural a resolverse con notificaciones (Gate 4).
- El historial en memoria está acotado (`HISTORY_SIZE`, 50 por defecto); el registro duradero
  es el `.jsonl` por aplicación, **que hoy no rota** — anotado como deuda para Gate 4.
- Impacto en threat model: mitiga **T9** (despliegues solapados dejando estado inconsistente).
  Un atacante sin el secreto no llega a encolar nada: la validación precede a la cola.
