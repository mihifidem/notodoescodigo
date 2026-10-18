# Duki Arenas portal integration contract

## Ownership and configuration

Django owns destination registration, portal availability, access checks, and the
player checkpoint. Phaser receives portal geometry and an opaque same-origin
launch URL; it never supplies a destination URL or grants access. Configure
destinations and portal geometry in Django Admin. Portal geometry is in map
pixels (`x`, `y`, `area_width`, `area_height`), and portal codes are unique
within a map.

`GameDestination.entry_url_name` must be a real Django URL name in that
destination's `owner_app` namespace. A destination with no confirmed route
remains `pending`; do not activate it or invent a URL. The only route currently
confirmed by the project is `duckyclash:home`.

The only currently implemented access requirement is
`{"min_total_points": <non-negative integer>}`. It is checked server-side against
the authenticated user's platform points. Unknown requirement keys fail closed
until the owning team and Core agree on their meaning.

## Launch flow

1. The player interacts with an available portal in the browser.
2. The browser POSTs the current state to
   `ducky_arenas:portal_launch`, using the named URL supplied in the world
   configuration and same-origin session/CSRF protection.
3. Django validates the authenticated player, unlocked era, available map,
   active portal, interaction bounds, destination registration, route namespace,
   and access requirements.
4. Only after authorization does Django persist the checkpoint using the
   existing versioned profile persistence. The response includes a server-built
   `redirectUrl` (with the authenticated Core return route attached as the
   `return_url` query parameter) and the same `returnUrl` as a separate field.
5. The destination application must use the provided `returnUrl` for its
   “return to Duki Arenas” action. It must not accept a client-supplied map,
   position, or destination URL as authority.

Example request body:

```json
{
  "era": "PRE",
  "x": 420,
  "y": 610,
  "direction": "right",
  "version": 3
}
```

The `x` and `y` values above are Phaser world coordinates in map pixels. The
API validates map bounds, configured walkable/blocked areas, era access, and the
profile's current map before persisting them. It does not accept a map switch
through the periodic state endpoint.

The periodic state endpoint uses the profile's optimistic `version`, performs
no database write for an unchanged checkpoint, and limits changed saves to the
configured `DUCKY_ARENAS_CHECKPOINT_INTERVAL_MS` interval (default: 5000 ms).
Portal launch is an explicit checkpoint and is not subject to that periodic
save interval.

## Map transition

To change eras, the browser POSTs the source era, current world position,
direction, and version to `ducky_arenas:map_transition`. Django verifies the
source map, player checkpoint version, target map availability, and the
player's server-authorized unlocks, then atomically saves the source map's
position and switches to the destination. If that player has a previously
saved, in-bounds, collision-free checkpoint for the destination, it is
restored; otherwise Django validates and uses that map's safe spawn. The
response reports `restoredCheckpoint` and redirects with a status marker so the
world page can explain whether the checkpoint or safe spawn was used. A GET to
a different era URL alone does not change the persisted map.

`PlayerMapCheckpoint` is scoped to the player's Core profile and map, with one
checkpoint per pair. The current position remains on `UserProfileArenas` for
compatibility with existing portal return flows. Movement checkpoints update
both records in one transaction; the client still uses optimistic versioning
and cannot select a map or position outside server validation.

## Era unlocks and evolution notices

The Core synchronizes XP thresholds from the configured EcoMotor progress
provider. It records earned eras but does not store or award XP. An era may be
unlocked while its map is not yet available; for example, Actualidad can appear
as unlocked in the timeline without a navigable map. Map entry still requires
an available map and a server-recorded unlock.

New unlocks are presented in an accessible evolution dialog using the existing
historical character portrait when available. The player confirms each notice
through `ducky_arenas:evolution_notice_ack`; Django only acknowledges an era
already recorded as unlocked. Existing unlocks are marked as already notified
by the non-destructive migration to avoid emitting historical notices after
deployment. Portraits are display assets only and are not substituted for
transparent movement sprites.

The world configuration also exposes only the landscape files confirmed in
Django's static-file finders. The checked files are named
`DAR_lamdscape_{ERA}_{NUMBER}.png` (the original `lamdscape` spelling is
preserved); no landscape or map is inferred for Actualidad.

## Return flow

The authenticated Core return route is `ducky_arenas:portal_return`. It uses the
player's server-side checkpoint and redirects to the saved era when that era and
map are still available and unlocked; otherwise it falls back to the Core world
view, which uses a validated checkpoint or safe map spawn. The return URL is not
an authorization token. Applications sharing the platform session should render
a normal same-origin link to it. DuckyClash must consume the supplied `returnUrl`
in its own UI; the Core does not modify DuckyClash templates or game behavior.

## Destination registration status

Only DuckyClash has a confirmed route and is eligible to become available after
an administrator configures the portal geometry. Duki Escape, Quiz Arenas, Duki
Training, DuckyBank, DuckyShop, Cabaña del Chamán, QuacksApp, and Conjunto de
Dólmenes are seeded as pending catalog entries. Their owning teams must provide
the app namespace, named entry route, return behavior, and any access-requirement
contract before activation.

## Geometry debugging

When `DEBUG` is enabled, the world page provides a temporary geometry editor.
Click the two opposite corners of the portal area, then export the JSON and
copy the pixel values into Django Admin. The tool does not write to the database
or modify source image assets. Do not treat preview geometry as approved
production placement.
