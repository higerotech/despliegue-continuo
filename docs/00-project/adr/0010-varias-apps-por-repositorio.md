# ADR-0010: Un repositorio puede declarar varias apps, y el workflow elige cuál

* **Estado:** accepted
* **Fecha:** 2026-10-14
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0010
* **Supersede / Superseded-by:** enmienda a ADR-0007 (el índice del inventario)
* **Controles OWASP afectados:** A01 (control de acceso), A06 (diseño inseguro)

## Contexto

Origina: RF04. El inventario se indexaba por `repo`, y `config.load_apps` rechazaba un repo
declarado dos veces. La regla era deliberada: con una sola app por repo, un evento de ese repo
solo podía corresponder a una app, y no había nada que decidir.

Esa regla dejó fuera a los monorepos. El primer caso real es un repositorio con seis servicios,
cada uno con su imagen y su workflow de build. El receptor solo podía desplegar uno; los otros
cinco se copiaban a mano al servidor, sin registro ni digest, que es justo lo que el despliegue
continuo existe para evitar.

## Decisión

**Un repositorio puede declarar varias apps.** El inventario pasa a indexarse por `name`, que
ya era la identidad de la app en todo lo demás: proyecto compose, fichero de estado y cola.

**Un evento sigue correspondiendo, como mucho, a una app.** Cuando un repo declara más de una:

1. todas deben usar `event: workflow_run`;
2. todas deben definir `workflow`;
3. no puede haber dos con el mismo `workflow`.

El nombre del workflow que llega en el payload elige la app. Si no coincide con ninguno, la
respuesta es `202 ignored` y enumera los esperados.

**Las tres condiciones se comprueban al cargar el inventario, no al recibir el webhook.** Un
inventario ambiguo impide arrancar, y `POST /reload` lo rechaza con `400` conservando el
anterior. La alternativa —resolver la ambigüedad en caliente, eligiendo la primera que encaje—
desplegaría la app equivocada sin avisar.

**Para un repo con una sola app no cambia nada:** ni el emparejamiento, ni los motivos de
`ignored`, ni el esquema. `workflow` sigue siendo opcional ahí.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo |
|---|---|---|---|
| **Índice por nombre; el workflow elige (elegida)** | Sin campos nuevos; una app sigue siendo un proyecto compose con su estado y su rollback | Un workflow por servicio en el repo de la app | Bajo: la ambigüedad se rechaza al cargar |
| Una sola app para todo el monorepo | Cero cambios en el receptor; lo que corre es un commit exacto | Cada release recrea todos los contenedores, cambien o no; el rollback es de todo o nada | Medio: un despliegue corta tareas largas de servicios que no cambiaron |
| Un evento despliega varias apps (`fan-out`) | Un solo workflow de build | Rompe el contrato de `/webhook`, que responde con **una** app y **una** posición en la cola; el rollback de una deja a las demás en otra versión | Alto |
| Elegir la app por las rutas que tocó el commit | No obliga a un workflow por servicio | El payload de `workflow_run` no trae las rutas: habría que llamar a la API de GitHub con un token, que el receptor hoy no necesita | Alto |

## Consecuencias

- Positivas: un monorepo despliega cada servicio por separado, con su propio historial, su
  propio `previous_tag` y su propio rollback. Solo se recrea lo que cambió.
- Positivas: la allowlist de ADR-0007 no se debilita. Sigue siendo estricta en repo, rama, tipo
  de evento y workflow; el workflow pasa además a seleccionar. No se usa ningún dato nuevo del
  payload.
- Negativas / deuda asumida: los workflows que no despliegan —tests, lint, seguridad— también
  emiten `workflow_run`, y en un repo con varias apps cada uno produce un `ignored` en el log.
  Es ruido esperado, no un fallo.
- Negativas / deuda asumida: un servicio sin endpoint HTTP no tiene `health_url`, así que su
  despliegue se da por bueno en cuanto arranca el contenedor y no hay rollback automático
  (DS-06). Los monorepos traen más servicios de ese tipo —consumidores de cola, tareas de
  cron—, y esta decisión hace más visible una carencia que ya existía.
- Impacto en threat model: ninguno en **T5** ni en **T2**. Quien pueda escribir en el
  repositorio ya podía cambiar qué construye el workflow declarado; poder nombrar otro workflow
  del mismo repo no le da nada que no tuviera.

## Verificación

- `tests/test_config.py`: varias apps con workflows distintos se aceptan; el mismo workflow
  dos veces, una app sin `workflow` y una app `push` en un repo compartido se rechazan; el
  mismo `name` dos veces se rechaza.
- `tests/test_webhook.py`: en un repo con dos apps, cada workflow encola la suya; un workflow
  ajeno no encola ninguna; la rama sigue contando; la app única de otro repo se comporta igual
  con el inventario ampliado.
