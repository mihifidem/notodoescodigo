# Rama profesional

## Catálogo e identidad

`users.RamaProfesional` es un catálogo separado de roles y membresías. Su `code` es único y no se puede modificar una vez creada la rama. La migración `users.0016` crea de forma idempotente estas ramas iniciales:

| Código | Nombre |
| --- | --- |
| `informatica` | Informática |
| `marketing` | Marketing |
| `gestion-administrativa` | Gestión administrativa |
| `ingles` | Inglés |

La migración no asigna ramas a cuentas existentes. `User.rama_principal` es opcional y usa `SET_NULL`, de modo que desactivar una rama la oculta de nuevas selecciones y mantiene referencias ya guardadas. No se borra una rama para retirarla.

## Selección y perfiles de CV

El selector está disponible durante el registro y en **Mi cuenta → Editar perfil** para cualquier plan. Puede elegirse «Sin seleccionar». La rama no cambia roles, plan, membresía, derechos de compra, permisos, número de perfiles ni acceso a plantillas.

Cada `curriculum.CVProfile` tiene su propia `rama_principal` opcional. Al crear un perfil se propone la rama activa de la cuenta; el usuario puede elegir otra. Los perfiles existentes no se actualizan cuando cambia la rama de la cuenta. Al desactivar una rama, los perfiles que ya la referencian conservan el dato histórico.

La rama es una preferencia para futuras recomendaciones, no una certificación, validación de idioma ni evaluación laboral. En particular, elegir Inglés no afirma nivel de idioma ni profesión. No se usa para descartar candidaturas ni exponer resultados privados.

## Administración y API

El catálogo se gestiona en Django Admin → **Usuarios → Ramas profesionales** por cuentas con acceso de administrador de plataforma (`is_platform_admin`). El código queda de solo lectura al editar y no se permite el borrado desde el administrador. Desactivar una rama conserva el historial.

- `GET /api/v1/professional-branches/` es público y devuelve solo ramas activas con `id`, `code`, `name` y `description`.
- `PATCH /api/v1/users/professional-branch/` requiere autenticación y cambia únicamente la rama de quien llama. Enviar `{"rama_principal": <id>}` selecciona una rama activa; enviar `{"rama_principal": null}` la limpia. Un ID inactivo o inexistente se rechaza.

La pertenencia se determina por la identidad autenticada, nunca por un `user_id` recibido del cliente.

## Guía de comprobación

1. Ejecuta `python manage.py migrate` para aplicar las migraciones de `users` y `curriculum`.
2. Entra con la cuenta existente `alumno1` usando su contraseña habitual; no es necesario modificar contraseña.
3. En **Mi cuenta → Editar perfil**, selecciona una rama y guarda. Vuelve a editar y comprueba que permanece seleccionada.
4. En My CV, crea dos perfiles: deja el primero con la rama propuesta y asigna al segundo una rama diferente. Cambia la rama principal de la cuenta y comprueba que los dos perfiles conservan sus propias elecciones.
5. Como administrador de plataforma, desactiva una rama del catálogo. Confirma que ya no se ofrece al registrar o crear/asignar ramas nuevas, pero que las cuentas y perfiles que ya la tenían mantienen su referencia.
6. Comprueba la API pública con `GET /api/v1/professional-branches/`. Autenticado como `alumno1`, prueba el `PATCH` con un ID activo y luego con uno inactivo. El segundo debe responder con error de validación. No se requieren credenciales de prueba ni restablecimientos.
