# ICASYS Documentos — edición ICASYS de WordCraft

**ICASYS Documentos** es una versión modificada de **WordCraft**, publicada por **ICASYS** (<https://icasys.mx>) para
uso educativo en sus aulas.

## Procedencia y atribución

- El programa original es **WordCraft**, creado por el **equipo y la comunidad de ArtCraft**
  (<https://getartcraft.com>). Todo el mérito del software es suyo y de sus colaboradores.
- El código original está bajo licencia **MIT o Apache-2.0, a elección del usuario**
  (`LICENSE-MIT`, `LICENSE-APACHE`). Esos archivos, el aviso de copyright
  ("Copyright (c) 2026 ArtCraft Team and the WordCraft contributors") y `NOTICE` se conservan sin cambios.
- Los materiales de terceros (fuentes, iconos, diccionarios, modelos) mantienen sus propias licencias,
  listadas en `NOTICE` y `ATTRIBUTION.md`.
- **El nombre y los logos de ArtCraft son marcas de ArtCraft Team y no se usan en esta edición.**
  Esta edición **no es un producto de ArtCraft**, ni está respaldada, patrocinada ni certificada por ellos.
  El soporte de ICASYS Documentos lo da ICASYS, no ArtCraft.
- Esta obra está basada en WordCraft del equipo de ArtCraft.

## Qué cambia respecto al original

Solo identidad visible; el motor del programa no se modifica.

- Nombre del producto en la interfaz, instaladores y ventanas: `WordCraft` → `ICASYS Documentos`.
- Iconos y logos: identidad gráfica de ICASYS.
- Enlaces de ayuda y "Acerca de": apuntan a <https://icasys.mx> y a <https://github.com/arodrigosote/wordcraft>
  en lugar de la comunidad y el sitio de ArtCraft.
- Nombres de archivos, identificadores internos, formatos y comandos **no** cambian, para poder
  incorporar las actualizaciones del proyecto original.

El resultado exacto está en el script `apply-branding.py` del repositorio `icasys-brand` y en el commit
"Edición ICASYS" de la rama `icasys`.

## Privacidad

Según la auditoría de ICASYS del código fuente del proyecto original (octubre de 2026): sin telemetría,
sin cuentas, sin servicios en la nube. Las únicas conexiones de red son opcionales y las inicia el usuario
(descarga de modelos de IA, búsqueda manual de actualizaciones). Esta edición no actualiza sola.

## Cómo se construye y se actualiza

La rama `icasys` es siempre **la versión de ArtCraft elegida + el commit de identidad ICASYS**. Para
actualizar se parte de una versión nueva del original, se reaplica el script de identidad y se compila
con `cargo build --release --locked`. Las versiones que usa ICASYS en sus aulas las decide ICASYS.

## Aviso

El software se ofrece "tal cual", sin garantías, igual que el original. Consulta las licencias para el detalle.
