# File Provenance Report

`openskp.provenance_report(path)` (Python, on `main`) inspects a `.skp` file — modern 2021+ VFF or classic SketchUp 3–2020 MFC — and returns everything the file records about where it came from, paired with the exact binary record, TLV tag path, or ZIP entry each item was read from and the entity it belongs to.

## What It Reports — and Never Guesses

The report records only what is stored in the file and never guesses. It never says `"created on 3 computers"` or `"written by X"`; it says **"3 different user folders appear in stored paths"** and lists where each path was read from.

> [!NOTE]
> **A user-folder name is not proof of authorship.** A texture image, component `.skp`, imported image, or `.style` file brought in from another person, library, or shared drive keeps the path stored when that asset was created or loaded, so paths from several machines or accounts routinely appear in a single model.

## Python API

```python
import openskp

report = openskp.provenance_report("model.skp")

print(report.format, report.sketchup_version, report.file_guid)
print(report.model_name, report.model_description)

for item in report.items:
    print(item.kind, item.value, "<-", item.source.format_short())

# Derived strictly from stored path fields (every item carries derived=True)
print(report.derived.distinct_user_folder_count, report.derived.user_folders)
print(report.derived.drive_letters, report.derived.network_shares)
print(report.derived.operating_systems)
```

Every `ProvenanceItem` has:
- `kind` (`str`) — one of the item kinds below;
- `value` — the stored string, Unix timestamp (`int`), or dictionary summary (`dict`);
- `source` (`ProvenanceSource`) — `record` (binary offset, TLV path, or ZIP entry), `entity_type` (`"file"`, `"model"`, `"definition"`, `"group_definition"`, `"image_definition"`, `"instance"`, `"group"`, `"image"`, `"material"`, `"layer"`, `"style"`, `"scene"`, `"face"`, `"edge"`, `"text"`, `"dimension"`, `"section_plane"`), and `entity_name`;
- `derived` (`bool`) — `False` for stored records in `report.items`, `True` for path-derived entries in `report.derived.items`.

`report.to_dict()` returns a JSON-serializable `dict` (adding ISO-8601 `"utc"` strings alongside Unix integer timestamps on `"save_time"` and `"definition_load_time"` items), and `report.format_text()` formats the human-readable report printed by the CLI.

## Command-Line Interface

```bash
# Readable text report
openskp info --provenance model.skp

# Machine-readable JSON report
openskp info --provenance --json model.skp
```

## Stored Item Kinds & Binary Sources

| `kind` | Value | 2021+ VFF source | Classic (SketchUp 3–2020) source | SketchUp C SDK getter |
|:---|:---|:---|:---|:---|
| `format` | `"vff"` or `"classic"` | `VFF file header` | `file header @0x0000` | — |
| `sketchup_version` | Version string, e.g. `"25.0.575"`, `"17.0.1"` | `VFF file header` (`meta/meta.dat > 6400 > 7500` if different) | `file header @0x0020` | `SUModelGetVersion` |
| `file_guid` | Lowercase UUID (`xxxxxxxx-xxxx-...`) | `meta/meta.dat > 6400 > 6600` | `file header @0x0034` | `SUModelGetGuid` |
| `definition_guid` | 22-character IFC Base64 GUID | `model.dat > F901 > 7017 > 7117 > 7C15 > 7D15` | `CComponentDefinition.guid` | `SUComponentDefinitionGetGuid` |
| `save_client` | Client identifier per VFF save-history entry | `meta/meta.dat > 6400 > 7800 > C800 > C900 > CA00[i] > CB00` | — | — |
| `save_time` | Unix `time_t` (`int`) | `meta/meta.dat > 6400 > 7800 > C800 > C900 > CA00[i] > CC00` | `file header` `u32` (after saved-path string) | — |
| `definition_load_time` | Unix `time_t` (`int`) | `model.dat > F901 > 7017 > 7117 > 7C15 > 8115` | `CComponentDefinition.load_time` | `SUComponentDefinitionGetLoadTime` |
| `model_name` | Model name string | `meta/meta.dat > 6400 > 6A00` / `model.dat > 0902 > ModelProperties['Name']` | `CAttributeNamed 'ModelProperties'['Name']` | `SUModelGetName` |
| `model_description` | Model description string | `meta/meta.dat > 6400 > 6B00` / `model.dat > 0902 > ModelProperties['Description']` | `CAttributeNamed 'ModelProperties'['Description']` / `CSketchUpModel` header description | `SUModelGetDescription` |
| `contributor_record` | `dict` of `GSU_ContributorsInfo` entries | `B336('GSU_ContributorsInfo')` on model or definition | `CAttributeNamed 'GSU_ContributorsInfo'` | `SUModelGetAttributeDictionaries` / `SUEntityGetAttributeDictionaries` |
| `model_path` | Previous `.skp` save path | `meta/meta.dat > 6400 > 6F00` | `file header @0x0044` (3rd UTF-16LE string) | — |
| `texture_path` | Source texture file path | `materials/<folder>/material.xml (mat:texture/@textureFilename)` | `CMaterial.tex_file`, `CLayer.tex_file` | `SUTextureGetFilePath` |
| `definition_path` | Loaded `.skp` component path | `model.dat > F901 > 7017 > 7117 > 7C15 > 8015` | `CComponentDefinition.path` | `SUComponentDefinitionGetPath` |
| `image_path` | Imported `Image` file path | Image `7C15 > 8015` & backing `<auto>` `material.xml@textureFilename` | `CImage.path` & backing `<auto>` `CMaterial.tex_file` | `SUImageGetFileName` |
| `style_path` | Source `.style` file path | `model.dat > 0602 > 7869 > 7969\|7B69 > 6C6B > 6E6B` | `CSkpStyle.path` | — |
| `geolocation` | `GeoReference` entries `dict` or `ShadowInfo` `dict` (`city`, `country`, `latitude`, `longitude`, `tz_offset`, `is_georeferenced`) | `B336('GeoReference')` and `model.dat > 0402 > 9065` | `CAttributeNamed 'GeoReference'` and `CShadowInfo` | `SUModelIsGeoReferenced`, `SULocationGetLatLong`, `SUShadowInfoGetValue` |
| `warehouse_dictionary` | `{"name", "entry_count", "entries"}` for `3DWarehouse` | `B336('3DWarehouse')` | `CAttributeNamed '3DWarehouse'` | `SUEntityGetAttributeDictionaries` |
| `plugin_dictionary` | `{"name", "entry_count"}` for non-built-in dictionaries | `B336(...)` on model, definitions, instances, groups, faces, edges, materials, layers, scenes, annotations | `CAttributeNamed` on model, definitions, instances, groups, faces, materials, layers | `SUModelGetAttributeDictionaries` / `SUEntityGetAttributeDictionaries` |
| `email_address` | Email address found inside a stored string | Exact string tag / XML attribute / dictionary key or value | Exact class field / dictionary key or value | — |
| `web_address` | `http://`, `https://`, or `www.` URL in a stored string | Exact string tag / XML attribute / dictionary key or value | Exact class field / dictionary key or value | — |

## Derived Path Summary (`report.derived`)

From `model_path`, `texture_path`, `definition_path`, `image_path`, and `style_path` items (including `file://` URIs), `report.derived` computes and marks with `derived=True`:

- **`user_folders`** (`distinct_user_folder_count`, item kind `derived_user_folder`) — account folder names from `X:\Users\<name>\...`, `X:\Documents and Settings\<name>\...`, `/Users/<name>/...`, and `/home/<name>/...`;
- **`drive_letters`** (`distinct_drive_letter_count`, item kind `derived_drive_letter`) — uppercase Windows drive roots (`"C:"`, `"D:"`, …);
- **`network_shares`** (`distinct_network_share_count`, item kind `derived_network_share`) — UNC server/share roots normalized as `\\server\share`;
- **`operating_systems`** (`distinct_operating_system_count`, item kind `derived_operating_system`) — `"Windows"`, `"macOS"`, `"Linux"`, or `"POSIX"` implied by each path's syntax or root prefix.
