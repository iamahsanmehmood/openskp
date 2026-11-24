"""DXF (AutoCAD Drawing Exchange Format R2000 / AC1015) 3D export module for OpenSKP.

Exports a baked :class:`~openskp.scene.Scene` directly to deterministic,
zero-dependency AutoCAD R2000 (``AC1015``) ASCII DXF with Polyface Mesh
(``POLYLINE`` / ``VERTEX``) or ``3DFACE`` entities grouped by layer and
standard ACI (Group 62) layer and entity material colors.

Unlike older releases that delegated ``polyface`` exports to optional
``ezdxf`` when installed, this module always writes the native R2000 stream
directly with per-triangle polyface vertices and ACI Group 62 colors (no
``ezdxf`` vertex deduplication or R2004+ True Color Group 420), matching the
C++, Rust, TypeScript, .NET, and Dart exporters byte-for-byte.
"""

from __future__ import annotations

import math
import pathlib
from typing import TYPE_CHECKING, Literal, Union

if TYPE_CHECKING:
    from ..scene import Scene

# 1 metre = 39.37007874015748 inches (SketchUp native unit)
METRES_TO_INCHES = 39.37007874015748

# Static AutoCAD R2000 (AC1015) boilerplate, extracted byte-for-byte from a
# real ezdxf-generated file independently confirmed to open cleanly in
# desktop AutoCAD - not just lenient third-party readers. Desktop AutoCAD
# validates far more strictly than ezdxf.readfile() (or ezdxf's own
# .audit(), which also reports zero errors on the pre-fix output below) or
# web viewers do: it needs the full CLASSES table, a real *Active VPORT
# record, and a complete OBJECTS dictionary tree (root dictionary plus
# LAYOUT records for Model/Layout1), or it silently refuses to open the
# file. Split into three header pieces so the LAYER table's per-scene
# entries can be spliced between the built-in "0"/"Defpoints" records
# (_HEADER_STATIC_B) and the rest of TABLES/BLOCKS (_HEADER_STATIC_C);
# _OBJECTS_STATIC needs no splicing - it carries no per-scene data at all.
# Every handle in this scaffold is a small hex literal well below 0x691,
# where dynamic (layer/entity) handles start - matching the exact starting
# handle the reference file itself uses for its own first entity, so every
# handle in an exported file stays globally unique ("monomorphic").

_HEADER_STATIC_A = [
        '  0', 'SECTION', '  2', 'HEADER', '  9', '$ACADVER', '  1', 'AC1015', '  9', '$ACADMAINTVER',
        ' 70', '6', '  9', '$DWGCODEPAGE', '  3', 'ANSI_1252', '  9', '$INSBASE', ' 10', '0.0',
        ' 20', '0.0', ' 30', '0.0', '  9', '$EXTMIN', ' 10', '1e+20', ' 20', '1e+20',
        ' 30', '1e+20', '  9', '$EXTMAX', ' 10', '-1e+20', ' 20', '-1e+20', ' 30', '-1e+20',
        '  9', '$LIMMIN', ' 10', '0.0', ' 20', '0.0', '  9', '$LIMMAX', ' 10', '420.0',
        ' 20', '297.0', '  9', '$ORTHOMODE', ' 70', '0', '  9', '$REGENMODE', ' 70', '1',
        '  9', '$FILLMODE', ' 70', '1', '  9', '$QTEXTMODE', ' 70', '0', '  9', '$MIRRTEXT',
        ' 70', '1', '  9', '$LTSCALE', ' 40', '1.0', '  9', '$ATTMODE', ' 70', '1',
        '  9', '$TEXTSIZE', ' 40', '2.5', '  9', '$TRACEWID', ' 40', '1.0', '  9', '$TEXTSTYLE',
        '  7', 'Standard', '  9', '$CLAYER', '  8', '0', '  9', '$CELTYPE', '  6', 'ByLayer',
        '  9', '$CECOLOR', ' 62', '256', '  9', '$CELTSCALE', ' 40', '1.0', '  9', '$DISPSILH',
        ' 70', '0', '  9', '$DIMSCALE', ' 40', '1.0', '  9', '$DIMASZ', ' 40', '2.5',
        '  9', '$DIMEXO', ' 40', '0.625', '  9', '$DIMDLI', ' 40', '3.75', '  9', '$DIMRND',
        ' 40', '0.0', '  9', '$DIMDLE', ' 40', '0.0', '  9', '$DIMEXE', ' 40', '1.25',
        '  9', '$DIMTP', ' 40', '0.0', '  9', '$DIMTM', ' 40', '0.0', '  9', '$DIMTXT',
        ' 40', '2.5', '  9', '$DIMCEN', ' 40', '2.5', '  9', '$DIMTSZ', ' 40', '0.0',
        '  9', '$DIMTOL', ' 70', '0', '  9', '$DIMLIM', ' 70', '0', '  9', '$DIMTIH',
        ' 70', '0', '  9', '$DIMTOH', ' 70', '0', '  9', '$DIMSE1', ' 70', '0',
        '  9', '$DIMSE2', ' 70', '0', '  9', '$DIMTAD', ' 70', '1', '  9', '$DIMZIN',
        ' 70', '8', '  9', '$DIMBLK', '  1', '', '  9', '$DIMASO', ' 70', '1',
        '  9', '$DIMSHO', ' 70', '1', '  9', '$DIMPOST', '  1', '', '  9', '$DIMAPOST',
        '  1', '', '  9', '$DIMALT', ' 70', '0', '  9', '$DIMALTD', ' 70', '3',
        '  9', '$DIMALTF', ' 40', '0.03937007874', '  9', '$DIMLFAC', ' 40', '1.0', '  9', '$DIMTOFL',
        ' 70', '1', '  9', '$DIMTVP', ' 40', '0.0', '  9', '$DIMTIX', ' 70', '0',
        '  9', '$DIMSOXD', ' 70', '0', '  9', '$DIMSAH', ' 70', '0', '  9', '$DIMBLK1',
        '  1', '', '  9', '$DIMBLK2', '  1', '', '  9', '$DIMSTYLE', '  2', 'ISO-25',
        '  9', '$DIMCLRD', ' 70', '0', '  9', '$DIMCLRE', ' 70', '0', '  9', '$DIMCLRT',
        ' 70', '0', '  9', '$DIMTFAC', ' 40', '1.0', '  9', '$DIMGAP', ' 40', '0.625',
        '  9', '$DIMJUST', ' 70', '0', '  9', '$DIMSD1', ' 70', '0', '  9', '$DIMSD2',
        ' 70', '0', '  9', '$DIMTOLJ', ' 70', '0', '  9', '$DIMTZIN', ' 70', '8',
        '  9', '$DIMALTZ', ' 70', '0', '  9', '$DIMALTTZ', ' 70', '0', '  9', '$DIMUPT',
        ' 70', '0', '  9', '$DIMDEC', ' 70', '2', '  9', '$DIMTDEC', ' 70', '2',
        '  9', '$DIMALTU', ' 70', '2', '  9', '$DIMALTTD', ' 70', '3', '  9', '$DIMTXSTY',
        '  7', 'Standard', '  9', '$DIMAUNIT', ' 70', '0', '  9', '$DIMADEC', ' 70', '0',
        '  9', '$DIMALTRND', ' 40', '0.0', '  9', '$DIMAZIN', ' 70', '0', '  9', '$DIMDSEP',
        ' 70', '44', '  9', '$DIMATFIT', ' 70', '3', '  9', '$DIMFRAC', ' 70', '0',
        '  9', '$DIMLDRBLK', '  1', '', '  9', '$DIMLUNIT', ' 70', '2', '  9', '$DIMLWD',
        ' 70', '-2', '  9', '$DIMLWE', ' 70', '-2', '  9', '$DIMTMOVE', ' 70', '0',
        '  9', '$LUNITS', ' 70', '2', '  9', '$LUPREC', ' 70', '4', '  9', '$SKETCHINC',
        ' 40', '1.0', '  9', '$FILLETRAD', ' 40', '10.0', '  9', '$AUNITS', ' 70', '0',
        '  9', '$AUPREC', ' 70', '2', '  9', '$MENU', '  1', '.', '  9', '$ELEVATION',
        ' 40', '0.0', '  9', '$PELEVATION', ' 40', '0.0', '  9', '$THICKNESS', ' 40', '0.0',
        '  9', '$LIMCHECK', ' 70', '0', '  9', '$CHAMFERA', ' 40', '0.0', '  9', '$CHAMFERB',
        ' 40', '0.0', '  9', '$CHAMFERC', ' 40', '0.0', '  9', '$CHAMFERD', ' 40', '0.0',
        '  9', '$SKPOLY', ' 70', '0', '  9', '$TDCREATE', ' 40', '2461265.626689815', '  9', '$TDUCREATE',
        ' 40', '2458532.153996898', '  9', '$TDUPDATE', ' 40', '2461265.626689815', '  9', '$TDUUPDATE', ' 40', '2458532.1544311',
        '  9', '$TDINDWG', ' 40', '0.0', '  9', '$TDUSRTIMER', ' 40', '0.0', '  9', '$USRTIMER',
        ' 70', '1', '  9', '$ANGBASE', ' 50', '0.0', '  9', '$ANGDIR', ' 70', '0',
        '  9', '$PDMODE', ' 70', '0', '  9', '$PDSIZE', ' 40', '0.0', '  9', '$PLINEWID',
        ' 40', '0.0', '  9', '$SPLFRAME', ' 70', '0', '  9', '$SPLINETYPE', ' 70', '6',
        '  9', '$SPLINESEGS', ' 70', '8', '  9', '$HANDSEED', '  5', '__HANDSEED__', '  9', '$SURFTAB1',
        ' 70', '6', '  9', '$SURFTAB2', ' 70', '6', '  9', '$SURFTYPE', ' 70', '6',
        '  9', '$SURFU', ' 70', '6', '  9', '$SURFV', ' 70', '6', '  9', '$UCSBASE',
        '  2', '', '  9', '$UCSNAME', '  2', '', '  9', '$UCSORG', ' 10', '0.0',
        ' 20', '0.0', ' 30', '0.0', '  9', '$UCSXDIR', ' 10', '1.0', ' 20', '0.0',
        ' 30', '0.0', '  9', '$UCSYDIR', ' 10', '0.0', ' 20', '1.0', ' 30', '0.0',
        '  9', '$UCSORTHOREF', '  2', '', '  9', '$UCSORTHOVIEW', ' 70', '0', '  9', '$UCSORGTOP',
        ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  9', '$UCSORGBOTTOM', ' 10', '0.0',
        ' 20', '0.0', ' 30', '0.0', '  9', '$UCSORGLEFT', ' 10', '0.0', ' 20', '0.0',
        ' 30', '0.0', '  9', '$UCSORGRIGHT', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
        '  9', '$UCSORGFRONT', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  9', '$UCSORGBACK',
        ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  9', '$PUCSBASE', '  2', '',
        '  9', '$PUCSNAME', '  2', '', '  9', '$PUCSORG', ' 10', '0.0', ' 20', '0.0',
        ' 30', '0.0', '  9', '$PUCSXDIR', ' 10', '1.0', ' 20', '0.0', ' 30', '0.0',
        '  9', '$PUCSYDIR', ' 10', '0.0', ' 20', '1.0', ' 30', '0.0', '  9', '$PUCSORTHOREF',
        '  2', '', '  9', '$PUCSORTHOVIEW', ' 70', '0', '  9', '$PUCSORGTOP', ' 10', '0.0',
        ' 20', '0.0', ' 30', '0.0', '  9', '$PUCSORGBOTTOM', ' 10', '0.0', ' 20', '0.0',
        ' 30', '0.0', '  9', '$PUCSORGLEFT', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
        '  9', '$PUCSORGRIGHT', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  9', '$PUCSORGFRONT',
        ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  9', '$PUCSORGBACK', ' 10', '0.0',
        ' 20', '0.0', ' 30', '0.0', '  9', '$USERI1', ' 70', '0', '  9', '$USERI2',
        ' 70', '0', '  9', '$USERI3', ' 70', '0', '  9', '$USERI4', ' 70', '0',
        '  9', '$USERI5', ' 70', '0', '  9', '$USERR1', ' 40', '0.0', '  9', '$USERR2',
        ' 40', '0.0', '  9', '$USERR3', ' 40', '0.0', '  9', '$USERR4', ' 40', '0.0',
        '  9', '$USERR5', ' 40', '0.0', '  9', '$WORLDVIEW', ' 70', '1', '  9', '$SHADEDGE',
        ' 70', '3', '  9', '$SHADEDIF', ' 70', '70', '  9', '$TILEMODE', ' 70', '1',
        '  9', '$MAXACTVP', ' 70', '64', '  9', '$PINSBASE', ' 10', '0.0', ' 20', '0.0',
        ' 30', '0.0', '  9', '$PLIMCHECK', ' 70', '0', '  9', '$PEXTMIN', ' 10', '1e+20',
        ' 20', '1e+20', ' 30', '1e+20', '  9', '$PEXTMAX', ' 10', '-1e+20', ' 20', '-1e+20',
        ' 30', '-1e+20', '  9', '$PLIMMIN', ' 10', '0.0', ' 20', '0.0', '  9', '$PLIMMAX',
        ' 10', '420.0', ' 20', '297.0', '  9', '$UNITMODE', ' 70', '0', '  9', '$VISRETAIN',
        ' 70', '1', '  9', '$PLINEGEN', ' 70', '0', '  9', '$PSLTSCALE', ' 70', '1',
        '  9', '$TREEDEPTH', ' 70', '3020', '  9', '$CMLSTYLE', '  2', 'Standard', '  9', '$CMLJUST',
        ' 70', '0', '  9', '$CMLSCALE', ' 40', '20.0', '  9', '$PROXYGRAPHICS', ' 70', '1',
        '  9', '$MEASUREMENT', ' 70', '1', '  9', '$CELWEIGHT', '370', '-1', '  9', '$ENDCAPS',
        '280', '0', '  9', '$JOINSTYLE', '280', '0', '  9', '$LWDISPLAY', '290', '0',
        '  9', '$INSUNITS', ' 70', '1', '  9', '$HYPERLINKBASE', '  1', '', '  9', '$STYLESHEET',
        '  1', '', '  9', '$XEDIT', '290', '1', '  9', '$CEPSNTYPE', '380', '0',
        '  9', '$PSTYLEMODE', '290', '1', '  9', '$FINGERPRINTGUID', '  2', '{901E6446-C8CA-4381-B5FE-8494D931A798}', '  9', '$VERSIONGUID',
        '  2', '{4B5A3BC4-57FB-4960-955B-D909E47DC28A}', '  9', '$EXTNAMES', '290', '1', '  9', '$PSVPSCALE', ' 40', '0.0',
        '  9', '$OLESTARTUP', '290', '0', '  0', 'ENDSEC', '  0', 'SECTION', '  2', 'CLASSES',
        '  0', 'CLASS', '  1', 'ACDBDICTIONARYWDFLT', '  2', 'AcDbDictionaryWithDefault', '  3', 'ObjectDBX Classes', ' 90', '0',
        '280', '0', '281', '0', '  0', 'CLASS', '  1', 'SUN', '  2', 'AcDbSun',
        '  3', 'SCENEOE', ' 90', '1153', '280', '0', '281', '0', '  0', 'CLASS',
        '  1', 'VISUALSTYLE', '  2', 'AcDbVisualStyle', '  3', 'ObjectDBX Classes', ' 90', '4095', '280', '0',
        '281', '0', '  0', 'CLASS', '  1', 'MATERIAL', '  2', 'AcDbMaterial', '  3', 'ObjectDBX Classes',
        ' 90', '1153', '280', '0', '281', '0', '  0', 'CLASS', '  1', 'SCALE',
        '  2', 'AcDbScale', '  3', 'ObjectDBX Classes', ' 90', '1153', '280', '0', '281', '0',
        '  0', 'CLASS', '  1', 'TABLESTYLE', '  2', 'AcDbTableStyle', '  3', 'ObjectDBX Classes', ' 90', '4095',
        '280', '0', '281', '0', '  0', 'CLASS', '  1', 'MLEADERSTYLE', '  2', 'AcDbMLeaderStyle',
        '  3', 'ACDB_MLEADERSTYLE_CLASS', ' 90', '4095', '280', '0', '281', '0', '  0', 'CLASS',
        '  1', 'DICTIONARYVAR', '  2', 'AcDbDictionaryVar', '  3', 'ObjectDBX Classes', ' 90', '0', '280', '0',
        '281', '0', '  0', 'CLASS', '  1', 'CELLSTYLEMAP', '  2', 'AcDbCellStyleMap', '  3', 'ObjectDBX Classes',
        ' 90', '1152', '280', '0', '281', '0', '  0', 'CLASS', '  1', 'MENTALRAYRENDERSETTINGS',
        '  2', 'AcDbMentalRayRenderSettings', '  3', 'SCENEOE', ' 90', '1024', '280', '0', '281', '0',
        '  0', 'CLASS', '  1', 'ACDBDETAILVIEWSTYLE', '  2', 'AcDbDetailViewStyle', '  3', 'ObjectDBX Classes', ' 90', '1025',
        '280', '0', '281', '0', '  0', 'CLASS', '  1', 'ACDBSECTIONVIEWSTYLE', '  2', 'AcDbSectionViewStyle',
        '  3', 'ObjectDBX Classes', ' 90', '1025', '280', '0', '281', '0', '  0', 'CLASS',
        '  1', 'RASTERVARIABLES', '  2', 'AcDbRasterVariables', '  3', 'ISM', ' 90', '0', '280', '0',
        '281', '0', '  0', 'CLASS', '  1', 'ACDBPLACEHOLDER', '  2', 'AcDbPlaceHolder', '  3', 'ObjectDBX Classes',
        ' 90', '0', '280', '0', '281', '0', '  0', 'CLASS', '  1', 'LAYOUT',
        '  2', 'AcDbLayout', '  3', 'ObjectDBX Classes', ' 90', '0', '280', '0', '281', '0',
        '  0', 'ENDSEC', '  0', 'SECTION', '  2', 'TABLES', '  0', 'TABLE', '  2', 'VPORT',
        '  5', '8', '330', '0', '100', 'AcDbSymbolTable', ' 70', '1', '  0', 'VPORT',
        '  5', '23', '330', '8', '100', 'AcDbSymbolTableRecord', '100', 'AcDbViewportTableRecord', '  2', '*Active',
        ' 70', '0', ' 10', '0.0', ' 20', '0.0', ' 11', '1.0', ' 21', '1.0',
        ' 12', '0.0', ' 22', '0.0', ' 13', '0.0', ' 23', '0.0', ' 14', '0.5',
        ' 24', '0.5', ' 15', '0.5', ' 25', '0.5', ' 16', '0.0', ' 26', '0.0',
        ' 36', '1.0', ' 17', '0.0', ' 27', '0.0', ' 37', '0.0', ' 40', '1000.0',
        ' 41', '1.34', ' 42', '50.0', ' 43', '0.0', ' 44', '0.0', ' 50', '0.0',
        ' 51', '0.0', ' 71', '0', ' 72', '1000', ' 73', '1', ' 74', '3',
        ' 75', '0', ' 76', '0', ' 77', '0', ' 78', '0', '281', '0',
        ' 65', '0', '146', '0.0', '  0', 'ENDTAB', '  0', 'TABLE', '  2', 'LTYPE',
        '  5', '2', '330', '0', '100', 'AcDbSymbolTable', ' 70', '3', '  0', 'LTYPE',
        '  5', '24', '330', '2', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLinetypeTableRecord', '  2', 'ByBlock',
        ' 70', '0', '  3', '', ' 72', '65', ' 73', '0', ' 40', '0.0',
        '  0', 'LTYPE', '  5', '25', '330', '2', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLinetypeTableRecord',
        '  2', 'ByLayer', ' 70', '0', '  3', '', ' 72', '65', ' 73', '0',
        ' 40', '0.0', '  0', 'LTYPE', '  5', '26', '330', '2', '100', 'AcDbSymbolTableRecord',
        '100', 'AcDbLinetypeTableRecord', '  2', 'Continuous', ' 70', '0', '  3', '', ' 72', '65',
        ' 73', '0', ' 40', '0.0', '  0', 'ENDTAB', '  0', 'TABLE', '  2', 'LAYER',
        '  5', '1', '330', '0', '100', 'AcDbSymbolTable', ' 70',
    ]

_HEADER_STATIC_B = [
        '  0', 'LAYER', '  5', '27', '330', '1', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLayerTableRecord',
        '  2', '0', ' 70', '0', ' 62', '7', '  6', 'Continuous', '370', '-3',
        '390', '13', '  0', 'LAYER', '  5', '28', '330', '1', '100', 'AcDbSymbolTableRecord',
        '100', 'AcDbLayerTableRecord', '  2', 'Defpoints', ' 70', '0', ' 62', '7', '  6', 'Continuous',
        '290', '0', '370', '-3', '390', '13',
    ]

_HEADER_STATIC_C = [
        '  0', 'ENDTAB', '  0', 'TABLE', '  2', 'STYLE', '  5', '5', '330', '0',
        '100', 'AcDbSymbolTable', ' 70', '1', '  0', 'STYLE', '  5', '29', '330', '5',
        '100', 'AcDbSymbolTableRecord', '100', 'AcDbTextStyleTableRecord', '  2', 'Standard', ' 70', '0', ' 40', '0.0',
        ' 41', '1.0', ' 50', '0.0', ' 71', '0', ' 42', '2.5', '  3', 'txt',
        '  4', '', '  0', 'ENDTAB', '  0', 'TABLE', '  2', 'VIEW', '  5', '7',
        '330', '0', '100', 'AcDbSymbolTable', ' 70', '0', '  0', 'ENDTAB', '  0', 'TABLE',
        '  2', 'UCS', '  5', '6', '330', '0', '100', 'AcDbSymbolTable', ' 70', '0',
        '  0', 'ENDTAB', '  0', 'TABLE', '  2', 'APPID', '  5', '3', '330', '0',
        '100', 'AcDbSymbolTable', ' 70', '3', '  0', 'APPID', '  5', '2A', '330', '3',
        '100', 'AcDbSymbolTableRecord', '100', 'AcDbRegAppTableRecord', '  2', 'ACAD', ' 70', '0', '  0', 'APPID',
        '  5', '2F', '330', '3', '100', 'AcDbSymbolTableRecord', '100', 'AcDbRegAppTableRecord', '  2', 'HATCHBACKGROUNDCOLOR',
        ' 70', '0', '  0', 'APPID', '  5', '30', '330', '3', '100', 'AcDbSymbolTableRecord',
        '100', 'AcDbRegAppTableRecord', '  2', 'EZDXF', ' 70', '0', '  0', 'ENDTAB', '  0', 'TABLE',
        '  2', 'DIMSTYLE', '  5', '4', '330', '0', '100', 'AcDbSymbolTable', ' 70', '1',
        '100', 'AcDbDimStyleTable', '  0', 'DIMSTYLE', '105', '2B', '330', '4', '100', 'AcDbSymbolTableRecord',
        '100', 'AcDbDimStyleTableRecord', '  2', 'Standard', ' 70', '0', '  3', '', '  4', '',
        ' 40', '1.0', ' 41', '2.5', ' 42', '0.625', ' 43', '3.75', ' 44', '1.25',
        ' 45', '0.0', ' 46', '0.0', ' 47', '0.0', ' 48', '0.0', '140', '2.5',
        '141', '2.5', '142', '0.0', '143', '0.03937007874', '144', '1.0', '145', '0.0',
        '146', '1.0', '147', '0.625', '148', '0.0', ' 71', '0', ' 72', '0',
        ' 73', '0', ' 74', '0', ' 75', '0', ' 76', '0', ' 77', '1',
        ' 78', '8', ' 79', '3', '170', '0', '171', '3', '172', '1',
        '173', '0', '174', '0', '175', '0', '176', '0', '177', '0',
        '178', '0', '179', '2', '271', '2', '272', '2', '273', '2',
        '274', '3', '275', '0', '276', '0', '277', '2', '278', '44',
        '279', '0', '280', '0', '281', '0', '282', '0', '283', '0',
        '284', '8', '285', '0', '286', '0', '288', '0', '289', '3',
        '371', '-2', '372', '-2', '  0', 'ENDTAB', '  0', 'TABLE', '  2', 'BLOCK_RECORD',
        '  5', '9', '330', '0', '100', 'AcDbSymbolTable', ' 70', '2', '  0', 'BLOCK_RECORD',
        '  5', '17', '330', '9', '100', 'AcDbSymbolTableRecord', '100', 'AcDbBlockTableRecord', '  2', '*Model_Space',
        '340', '1A', '  0', 'BLOCK_RECORD', '  5', '1B', '330', '9', '100', 'AcDbSymbolTableRecord',
        '100', 'AcDbBlockTableRecord', '  2', '*Paper_Space', '340', '1E', '  0', 'ENDTAB', '  0', 'ENDSEC',
        '  0', 'SECTION', '  2', 'BLOCKS', '  0', 'BLOCK', '  5', '18', '330', '17',
        '100', 'AcDbEntity', '  8', '0', '100', 'AcDbBlockBegin', '  2', '*Model_Space', ' 70', '0',
        ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  3', '*Model_Space', '  1', '',
        '  0', 'ENDBLK', '  5', '19', '330', '17', '100', 'AcDbEntity', '  8', '0',
        '100', 'AcDbBlockEnd', '  0', 'BLOCK', '  5', '1C', '330', '1B', '100', 'AcDbEntity',
        '  8', '0', '100', 'AcDbBlockBegin', '  2', '*Paper_Space', ' 70', '0', ' 10', '0.0',
        ' 20', '0.0', ' 30', '0.0', '  3', '*Paper_Space', '  1', '', '  0', 'ENDBLK',
        '  5', '1D', '330', '1B', '100', 'AcDbEntity', '  8', '0', '100', 'AcDbBlockEnd',
        '  0', 'ENDSEC',
    ]

_OBJECTS_STATIC = [
        '  0', 'SECTION', '  2', 'OBJECTS', '  0', 'DICTIONARY', '  5', 'A', '330', '0',
        '100', 'AcDbDictionary', '281', '1', '  3', 'ACAD_COLOR', '350', 'B', '  3', 'ACAD_GROUP',
        '350', 'C', '  3', 'ACAD_LAYOUT', '350', 'D', '  3', 'ACAD_MATERIAL', '350', 'E',
        '  3', 'ACAD_MLEADERSTYLE', '350', 'F', '  3', 'ACAD_MLINESTYLE', '350', '10', '  3', 'ACAD_PLOTSETTINGS',
        '350', '11', '  3', 'ACAD_PLOTSTYLENAME', '350', '12', '  3', 'ACAD_SCALELIST', '350', '14',
        '  3', 'ACAD_TABLESTYLE', '350', '15', '  3', 'ACAD_VISUALSTYLE', '350', '16', '  3', 'EZDXF_META',
        '350', '2D', '  0', 'DICTIONARY', '  5', 'B', '330', 'A', '100', 'AcDbDictionary',
        '281', '1', '  0', 'DICTIONARY', '  5', 'C', '330', 'A', '100', 'AcDbDictionary',
        '281', '1', '  0', 'DICTIONARY', '  5', 'D', '330', 'A', '100', 'AcDbDictionary',
        '281', '1', '  3', 'Model', '350', '1A', '  3', 'Layout1', '350', '1E',
        '  0', 'DICTIONARY', '  5', 'E', '330', 'A', '100', 'AcDbDictionary', '281', '1',
        '  3', 'ByBlock', '350', '1F', '  3', 'ByLayer', '350', '20', '  3', 'Global',
        '350', '21', '  0', 'DICTIONARY', '  5', 'F', '330', 'A', '100', 'AcDbDictionary',
        '281', '1', '  3', 'Standard', '350', '2C', '  0', 'DICTIONARY', '  5', '10',
        '330', 'A', '100', 'AcDbDictionary', '281', '1', '  3', 'Standard', '350', '22',
        '  0', 'DICTIONARY', '  5', '11', '330', 'A', '100', 'AcDbDictionary', '281', '1',
        '  0', 'ACDBDICTIONARYWDFLT', '  5', '12', '330', 'A', '100', 'AcDbDictionary', '281', '1',
        '  3', 'Normal', '350', '13', '100', 'AcDbDictionaryWithDefault', '340', '13', '  0', 'ACDBPLACEHOLDER',
        '  5', '13', '330', '12', '  0', 'DICTIONARY', '  5', '14', '330', 'A',
        '100', 'AcDbDictionary', '281', '1', '  0', 'DICTIONARY', '  5', '15', '330', 'A',
        '100', 'AcDbDictionary', '281', '1', '  0', 'DICTIONARY', '  5', '16', '330', 'A',
        '100', 'AcDbDictionary', '281', '1', '  0', 'LAYOUT', '  5', '1A', '330', 'D',
        '100', 'AcDbPlotSettings', '  1', '', '  4', 'A3', '  6', '', ' 40', '7.5',
        ' 41', '20.0', ' 42', '7.5', ' 43', '20.0', ' 44', '420.0', ' 45', '297.0',
        ' 46', '0.0', ' 47', '0.0', ' 48', '0.0', ' 49', '0.0', '140', '0.0',
        '141', '0.0', '142', '1.0', '143', '1.0', ' 70', '1024', ' 72', '1',
        ' 73', '0', ' 74', '5', '  7', '', ' 75', '16', ' 76', '0',
        ' 77', '2', ' 78', '300', '147', '1.0', '148', '0.0', '149', '0.0',
        '100', 'AcDbLayout', '  1', 'Model', ' 70', '1', ' 71', '0', ' 10', '0.0',
        ' 20', '0.0', ' 11', '420.0', ' 21', '297.0', ' 12', '0.0', ' 22', '0.0',
        ' 32', '0.0', ' 14', '1e+20', ' 24', '1e+20', ' 34', '1e+20', ' 15', '-1e+20',
        ' 25', '-1e+20', ' 35', '-1e+20', '146', '0.0', ' 13', '0.0', ' 23', '0.0',
        ' 33', '0.0', ' 16', '1.0', ' 26', '0.0', ' 36', '0.0', ' 17', '0.0',
        ' 27', '1.0', ' 37', '0.0', ' 76', '1', '330', '17', '  0', 'LAYOUT',
        '  5', '1E', '330', 'D', '100', 'AcDbPlotSettings', '  1', '', '  4', 'A3',
        '  6', '', ' 40', '7.5', ' 41', '20.0', ' 42', '7.5', ' 43', '20.0',
        ' 44', '420.0', ' 45', '297.0', ' 46', '0.0', ' 47', '0.0', ' 48', '0.0',
        ' 49', '0.0', '140', '0.0', '141', '0.0', '142', '1.0', '143', '1.0',
        ' 70', '0', ' 72', '1', ' 73', '0', ' 74', '5', '  7', '',
        ' 75', '16', ' 76', '0', ' 77', '2', ' 78', '300', '147', '1.0',
        '148', '0.0', '149', '0.0', '100', 'AcDbLayout', '  1', 'Layout1', ' 70', '1',
        ' 71', '1', ' 10', '0.0', ' 20', '0.0', ' 11', '420.0', ' 21', '297.0',
        ' 12', '0.0', ' 22', '0.0', ' 32', '0.0', ' 14', '1e+20', ' 24', '1e+20',
        ' 34', '1e+20', ' 15', '-1e+20', ' 25', '-1e+20', ' 35', '-1e+20', '146', '0.0',
        ' 13', '0.0', ' 23', '0.0', ' 33', '0.0', ' 16', '1.0', ' 26', '0.0',
        ' 36', '0.0', ' 17', '0.0', ' 27', '1.0', ' 37', '0.0', ' 76', '1',
        '330', '1B', '  0', 'MATERIAL', '  5', '1F', '102', '{ACAD_REACTORS', '330', 'E',
        '102', '}', '330', 'E', '100', 'AcDbMaterial', '  1', 'ByBlock', '  2', '',
        ' 70', '0', ' 40', '1.0', ' 71', '1', ' 41', '1.0', ' 91', '-1023410177',
        ' 42', '1.0', ' 72', '1', '  3', '', ' 73', '1', ' 74', '1',
        ' 75', '1', ' 44', '0.5', ' 73', '0', ' 45', '1.0', ' 46', '1.0',
        ' 77', '1', '  4', '', ' 78', '1', ' 79', '1', '170', '1',
        ' 48', '1.0', '171', '1', '  6', '', '172', '1', '173', '1',
        '174', '1', '140', '1.0', '141', '1.0', '175', '1', '  7', '',
        '176', '1', '177', '1', '178', '1', '143', '1.0', '179', '1',
        '  8', '', '270', '1', '271', '1', '272', '1', '145', '1.0',
        '146', '1.0', '273', '1', '  9', '', '274', '1', '275', '1',
        '276', '1', ' 42', '1.0', ' 72', '1', '  3', '', ' 73', '1',
        ' 74', '1', ' 75', '1', ' 94', '63', '  0', 'MATERIAL', '  5', '20',
        '102', '{ACAD_REACTORS', '330', 'E', '102', '}', '330', 'E', '100', 'AcDbMaterial',
        '  1', 'ByLayer', '  2', '', ' 70', '0', ' 40', '1.0', ' 71', '1',
        ' 41', '1.0', ' 91', '-1023410177', ' 42', '1.0', ' 72', '1', '  3', '',
        ' 73', '1', ' 74', '1', ' 75', '1', ' 44', '0.5', ' 73', '0',
        ' 45', '1.0', ' 46', '1.0', ' 77', '1', '  4', '', ' 78', '1',
        ' 79', '1', '170', '1', ' 48', '1.0', '171', '1', '  6', '',
        '172', '1', '173', '1', '174', '1', '140', '1.0', '141', '1.0',
        '175', '1', '  7', '', '176', '1', '177', '1', '178', '1',
        '143', '1.0', '179', '1', '  8', '', '270', '1', '271', '1',
        '272', '1', '145', '1.0', '146', '1.0', '273', '1', '  9', '',
        '274', '1', '275', '1', '276', '1', ' 42', '1.0', ' 72', '1',
        '  3', '', ' 73', '1', ' 74', '1', ' 75', '1', ' 94', '63',
        '  0', 'MATERIAL', '  5', '21', '102', '{ACAD_REACTORS', '330', 'E', '102', '}',
        '330', 'E', '100', 'AcDbMaterial', '  1', 'Global', '  2', '', ' 70', '0',
        ' 40', '1.0', ' 71', '1', ' 41', '1.0', ' 91', '-1023410177', ' 42', '1.0',
        ' 72', '1', '  3', '', ' 73', '1', ' 74', '1', ' 75', '1',
        ' 44', '0.5', ' 73', '0', ' 45', '1.0', ' 46', '1.0', ' 77', '1',
        '  4', '', ' 78', '1', ' 79', '1', '170', '1', ' 48', '1.0',
        '171', '1', '  6', '', '172', '1', '173', '1', '174', '1',
        '140', '1.0', '141', '1.0', '175', '1', '  7', '', '176', '1',
        '177', '1', '178', '1', '143', '1.0', '179', '1', '  8', '',
        '270', '1', '271', '1', '272', '1', '145', '1.0', '146', '1.0',
        '273', '1', '  9', '', '274', '1', '275', '1', '276', '1',
        ' 42', '1.0', ' 72', '1', '  3', '', ' 73', '1', ' 74', '1',
        ' 75', '1', ' 94', '63', '  0', 'MLINESTYLE', '  5', '22', '102', '{ACAD_REACTORS',
        '330', '10', '102', '}', '330', '10', '100', 'AcDbMlineStyle', '  2', 'Standard',
        ' 70', '0', '  3', '', ' 62', '256', ' 51', '90.0', ' 52', '90.0',
        ' 71', '2', ' 49', '0.5', ' 62', '256', '  6', 'BYLAYER', ' 49', '-0.5',
        ' 62', '256', '  6', 'BYLAYER', '  0', 'MLEADERSTYLE', '  5', '2C', '102', '{ACAD_REACTORS',
        '330', 'F', '102', '}', '330', 'F', '100', 'AcDbMLeaderStyle', '179', '2',
        '170', '2', '171', '1', '172', '0', ' 90', '2', ' 40', '0.0',
        ' 41', '0.0', '173', '1', ' 91', '-1056964608', ' 92', '-2', '290', '1',
        ' 42', '2.0', '291', '1', ' 43', '8.0', '  3', 'Standard', ' 44', '4.0',
        '300', '', '342', '29', '174', '1', '175', '1', '176', '0',
        '178', '1', ' 93', '-1056964608', ' 45', '4.0', '292', '0', '297', '0',
        ' 46', '4.0', ' 94', '-1056964608', ' 47', '1.0', ' 49', '1.0', '140', '1.0',
        '294', '1', '141', '0.0', '177', '0', '142', '1.0', '295', '0',
        '296', '0', '143', '3.75', '271', '0', '272', '9', '273', '9',
        '  0', 'DICTIONARY', '  5', '2D', '330', 'A', '100', 'AcDbDictionary', '280', '1',
        '281', '1', '  3', 'CREATED_BY_EZDXF', '350', '2E', '  3', 'WRITTEN_BY_EZDXF', '350', '31',
        '  0', 'DICTIONARYVAR', '  5', '2E', '330', '2D', '100', 'DictionaryVariables', '280', '0',
        '  1', '1.4.3 @ 2026-08-12T10:02:26.972595+00:00', '  0', 'DICTIONARYVAR', '  5', '31', '330', '2D', '100', 'DictionaryVariables',
        '280', '0', '  1', '1.4.3 @ 2026-08-12T10:02:26.973142+00:00', '  0', 'ENDSEC', '  0', 'EOF',
    ]


_ILLEGAL_LAYER_UNITS = {ord(c) for c in '<>/\\"~:;?*=`|'}
_TRIM_UNITS = {0x20, 0x09, 0x0D, 0x0A}
_MAX_LAYER_NAME_LEN = 255


def _name_to_raw_bytes(name: Union[str, bytes]) -> bytes:
    """Convert a layer name to raw bytes, recovering surrogateescape bytes and CESU-8 surrogates."""
    if isinstance(name, bytes):
        return name
    out = bytearray()
    for ch in name:
        cp = ord(ch)
        if 0xDC80 <= cp <= 0xDCFF:
            out.append(cp & 0xFF)
        elif 0xD800 <= cp <= 0xDFFF:
            out.append(0xE0 | (cp >> 12))
            out.append(0x80 | ((cp >> 6) & 0x3F))
            out.append(0x80 | (cp & 0x3F))
        else:
            out.extend(ch.encode("utf-8"))
    return bytes(out)


def _layer_name_utf16(raw: bytes) -> list[int]:
    """Decode UTF-8 bytes into UTF-16 code units, replacing malformed sequences with '_'."""
    result: list[int] = []
    i = 0
    n = len(raw)
    while i < n:
        lead = raw[i]
        i += 1
        cp = lead
        extra = 0
        minimum = 0
        if 0xC2 <= lead <= 0xDF:
            cp = lead & 0x1F
            extra = 1
            minimum = 0x80
        elif 0xE0 <= lead <= 0xEF:
            cp = lead & 0x0F
            extra = 2
            minimum = 0x800
        elif 0xF0 <= lead <= 0xF4:
            cp = lead & 0x07
            extra = 3
            minimum = 0x10000
        elif lead >= 0x80:
            result.append(0x5F)
            continue
        valid = True
        for _ in range(extra):
            if i == n or (raw[i] & 0xC0) != 0x80:
                valid = False
                break
            cp = (cp << 6) | (raw[i] & 0x3F)
            i += 1
        if not valid or cp < minimum or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
            result.append(0x5F)
            continue
        if cp > 0xFFFF:
            cp -= 0x10000
            result.append(0xD800 + (cp >> 10))
            result.append(0xDC00 + (cp & 0x3FF))
        else:
            result.append(cp)
    return result


def _layer_name_hash(raw: bytes) -> str:
    """32-bit FNV-1a hash of the source layer name bytes as 8 uppercase hex digits."""
    h = 2166136261
    for b in raw:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return f"{h:08X}"


def _sanitize_layer_name(name: Union[str, bytes], suffix: str = "") -> str:
    """Sanitize and CIF-escape a layer name for R2000 (AC1015 / ANSI_1252) DXF."""
    raw = _name_to_raw_bytes(name)
    units = _layer_name_utf16(raw)
    first = next((idx for idx, u in enumerate(units) if u not in _TRIM_UNITS), None)
    if first is None:
        clean = [0x30]
    else:
        last = max(idx for idx, u in enumerate(units) if u not in _TRIM_UNITS)
        clean = units[first : last + 1]
    for idx, c in enumerate(clean):
        if c < 32 or 127 <= c <= 159 or c in _ILLEGAL_LAYER_UNITS:
            clean[idx] = 0x5F
    if len(clean) > _MAX_LAYER_NAME_LEN and not suffix:
        suffix = "_" + _layer_name_hash(raw)
    if len(clean) + len(suffix) > _MAX_LAYER_NAME_LEN:
        end = _MAX_LAYER_NAME_LEN - len(suffix)
        if end > 0 and 0xD800 <= clean[end - 1] <= 0xDBFF:
            end -= 1
        clean = clean[:end]
    clean.extend(ord(ch) for ch in suffix)
    out: list[str] = []
    for c in clean:
        if c < 128:
            out.append(chr(c))
        else:
            out.append(f"\\U+{c:04X}")
    return "".join(out)


def _layer_name_key(name: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in name)


def _rgb_to_aci(r: int, g: int, b: int) -> int:
    """Map 0-255 RGB color to closest standard AutoCAD Color Index (ACI 1-255)."""
    standard_aci = (
        (255, 0, 0, 1),      # Red
        (255, 255, 0, 2),    # Yellow
        (0, 255, 0, 3),      # Green
        (0, 255, 255, 4),    # Cyan
        (0, 0, 255, 5),      # Blue
        (255, 0, 255, 6),    # Magenta
        (255, 255, 255, 7),  # White
        (128, 128, 128, 8),  # Dark Gray
        (192, 192, 192, 9),  # Light Gray
    )
    best_aci = 7
    min_dist = float("inf")
    for sr, sg, sb, aci in standard_aci:
        dist = (r - sr) ** 2 + (g - sg) ** 2 + (b - sb) ** 2
        if dist < min_dist:
            min_dist = dist
            best_aci = aci
    return best_aci


def _get_prim_rgb(scene: Scene, prim: object) -> tuple[int, int, int]:
    """Extract (R, G, B) integer tuple (0-255) for a primitive's material."""
    r, g, b = 200, 200, 200
    mat_idx = getattr(prim, "material_index", None)
    if mat_idx is not None and scene.gltf_materials and mat_idx < len(scene.gltf_materials):
        mat = scene.gltf_materials[mat_idx]
        if isinstance(mat, dict):
            pbr = mat.get("pbrMetallicRoughness", {})
            color_vec = pbr.get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
            if len(color_vec) >= 3:
                r = int(round(color_vec[0] * 255.0))
                g = int(round(color_vec[1] * 255.0))
                b = int(round(color_vec[2] * 255.0))
    return max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))


def to_dxf(
    scene: Scene,
    scale: float = METRES_TO_INCHES,
    mode: Literal["3dface", "polyface"] = "polyface",
) -> str:
    """Serialize a baked scene directly to AutoCAD R2000 (AC1015) 3D ASCII DXF.

    Writes deterministic R2000 output directly (without delegating to optional
    ``ezdxf``) using per-triangle polyface vertices and ACI Group 62 colors.
    Primitives exceeding signed 16-bit polyface vertex or face limits (32,767)
    automatically fall back to ``3DFACE`` triangles.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        scale: Scale factor for vertex coordinates (default: METRES_TO_INCHES).
        mode: Export entity mode ('polyface' for Polyface Meshes or '3dface' for 3DFACE entities).

    Returns:
        Formatted ASCII DXF text string with CRLF newlines.
    """
    if scene is None or scene.glb_primitives is None:
        raise ValueError("scene cannot be None")
    if not math.isfinite(scale):
        raise ValueError("DXF scale must be finite")

    # Zero-dependency native R2000 DXF exporter, structurally identical to
    # the confirmed-working reference (see _HEADER_STATIC_*/_OBJECTS_STATIC
    # above) - full desktop AutoCAD compliance, not just what ezdxf.readfile()
    # or a web viewer tolerates.
    surface_prims = scene.surface_primitives
    layer_colors: dict[str, tuple[int, int, int]] = {}
    layer_names: dict[Union[str, bytes], str] = {}
    used_names: set[str] = {"0", "defpoints"}
    for prim in surface_prims:
        if len(prim.indices) == 0:
            continue
        if len(prim.positions) % 3 != 0 or len(prim.indices) % 3 != 0:
            raise ValueError("DXF mesh has incomplete coordinates or triangles")
        vert_count = len(prim.positions) // 3
        for index in prim.indices:
            if index < 0 or index >= vert_count:
                raise ValueError("DXF mesh index is outside the vertex array")
        for coord in prim.positions:
            if not math.isfinite(coord * scale):
                raise ValueError("DXF mesh coordinates must be finite")
        raw_name: Union[str, bytes] = "" if prim.geom_name is None else prim.geom_name
        if raw_name in layer_names:
            continue
        raw_bytes = _name_to_raw_bytes(raw_name)
        layer_name = _sanitize_layer_name(raw_bytes)
        attempt = 0
        while _layer_name_key(layer_name) in used_names:
            suffix = "_" + _layer_name_hash(raw_bytes) + ("" if attempt == 0 else f"_{attempt}")
            layer_name = _sanitize_layer_name(raw_bytes, suffix)
            attempt += 1
        used_names.add(_layer_name_key(layer_name))
        layer_names[raw_name] = layer_name
        if layer_name not in layer_colors:
            layer_colors[layer_name] = _get_prim_rgb(scene, prim)

    sorted_layers = sorted(layer_colors.keys())

    # Every static handle above is well below 0x691 - starting dynamic
    # handles there (matching the reference file's own first entity handle)
    # keeps every handle in the file globally unique.
    handle_counter = 0x691

    def next_handle() -> str:
        nonlocal handle_counter
        h = f"{handle_counter:X}"
        handle_counter += 1
        return h

    lines = list(_HEADER_STATIC_A)
    # LAYER table record count: built-in "0" + "Defpoints" + this scene's layers.
    lines.append(str(len(sorted_layers) + 2))
    lines.extend(_HEADER_STATIC_B)

    for l_name in sorted_layers:
        r, g, b = layer_colors[l_name]
        aci = _rgb_to_aci(r, g, b)
        # Group 420 (24-bit true color) is an R2004+ addition - real ezdxf
        # never emits it for an R2000/AC1015 document even when true_color
        # is set on the layer, so it's dropped here too; ACI (62) is the
        # only color mechanism R2000 actually supports. 370/390 (lineweight,
        # plot style handle) are required on every LAYER record once the
        # drawing uses a Named plot style table - AutoCAD desktop rejects
        # the whole TABLES section ("Did not receive PlotStyleName") if any
        # layer omits them, even though ezdxf.readfile() doesn't care.
        lines.extend([
            "  0", "LAYER", "  5", next_handle(), "330", "1", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord",
            "  2", l_name, " 70", "0", " 62", str(aci), "  6", "Continuous", "370", "-3", "390", "13",
        ])

    lines.extend(_HEADER_STATIC_C)
    lines.extend(["  0", "SECTION", "  2", "ENTITIES"])

    for prim in surface_prims:
        tri_count = len(prim.indices) // 3
        if tri_count == 0:
            continue
        raw_name = "" if prim.geom_name is None else prim.geom_name
        layer_name = layer_names[raw_name]

        r, g, b = _get_prim_rgb(scene, prim)
        aci = _rgb_to_aci(r, g, b)

        # Polyface counts (71/72) and vertex indices (71-74) are signed
        # 16-bit fields. Larger meshes use independent 3DFACE triangles.
        v_count = len(prim.positions) // 3
        if mode == "polyface" and v_count <= 32767 and tri_count <= 32767:
            # Structure verified against real ezdxf's own render_polyface()
            # output byte-for-byte, not guessed: face-record VERTEX entries
            # carry a color (62) and dummy 0/0/0 coordinates but NOT the
            # AcDbVertex subclass marker that point-vertex entries have, and
            # SEQEND's owner (330) is the POLYLINE's own handle, not
            # Model_Space's - both were wrong here before and are exactly
            # why AutoCAD desktop rejected polyface-mode output while
            # accepting 3dface-mode output using the same scaffold.
            polyline_handle = next_handle()
            lines.extend([
                "  0", "POLYLINE", "  5", polyline_handle, "330", "17", "100", "AcDbEntity", "  8", layer_name,
                " 62", str(aci), "100", "AcDbPolyFaceMesh", " 66", "1",
                " 10", "0.0", " 20", "0.0", " 30", "0.0",
                " 70", "64", " 71", str(v_count), " 72", str(tri_count),
            ])
            for i in range(v_count):
                vx = f"{prim.positions[i * 3] * scale:.6f}"
                vy = f"{prim.positions[i * 3 + 1] * scale:.6f}"
                vz = f"{prim.positions[i * 3 + 2] * scale:.6f}"
                lines.extend([
                    "  0", "VERTEX", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name,
                    "100", "AcDbVertex", "100", "AcDbPolyFaceMeshVertex",
                    " 10", vx, " 20", vy, " 30", vz, " 70", "192",
                ])
            for i in range(tri_count):
                idx0 = prim.indices[i * 3] + 1
                idx1 = prim.indices[i * 3 + 1] + 1
                idx2 = prim.indices[i * 3 + 2] + 1
                lines.extend([
                    "  0", "VERTEX", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name,
                    " 62", str(aci), "100", "AcDbFaceRecord",
                    " 10", "0.0", " 20", "0.0", " 30", "0.0", " 70", "128",
                    " 71", str(idx0), " 72", str(idx1), " 73", str(idx2),
                ])
            lines.extend([
                "  0", "SEQEND", "  5", next_handle(), "330", polyline_handle, "100", "AcDbEntity", "  8", layer_name,
            ])
        else:
            for i in range(tri_count):
                i0 = prim.indices[i * 3]
                i1 = prim.indices[i * 3 + 1]
                i2 = prim.indices[i * 3 + 2]

                v0x = f"{prim.positions[i0 * 3] * scale:.6f}"
                v0y = f"{prim.positions[i0 * 3 + 1] * scale:.6f}"
                v0z = f"{prim.positions[i0 * 3 + 2] * scale:.6f}"

                v1x = f"{prim.positions[i1 * 3] * scale:.6f}"
                v1y = f"{prim.positions[i1 * 3 + 1] * scale:.6f}"
                v1z = f"{prim.positions[i1 * 3 + 2] * scale:.6f}"

                v2x = f"{prim.positions[i2 * 3] * scale:.6f}"
                v2y = f"{prim.positions[i2 * 3 + 1] * scale:.6f}"
                v2z = f"{prim.positions[i2 * 3 + 2] * scale:.6f}"

                lines.extend([
                    "  0", "3DFACE", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name,
                    " 62", str(aci), "100", "AcDbFace",
                    " 10", v0x, " 20", v0y, " 30", v0z,
                    " 11", v1x, " 21", v1y, " 31", v1z,
                    " 12", v2x, " 22", v2y, " 32", v2z,
                    " 13", v2x, " 23", v2y, " 33", v2z,
                ])

    lines.extend(["  0", "ENDSEC"])
    lines.extend(_OBJECTS_STATIC)

    text = "\r\n".join(lines) + "\r\n"
    text = text.replace("__HANDSEED__", f"{(handle_counter + 0x10):X}")
    return text


def export(
    scene: Scene,
    output_path: Union[str, pathlib.Path],
    scale: float = METRES_TO_INCHES,
    mode: Literal["3dface", "polyface"] = "polyface",
) -> None:
    """Export a baked scene to an AutoCAD R2000 3D DXF file.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        output_path: Destination file path (.dxf).
        scale: Scale factor for vertex coordinates (default: METRES_TO_INCHES).
        mode: Export entity mode ('polyface' or '3dface').
    """
    if scene is None:
        raise ValueError("scene cannot be None")

    text = to_dxf(scene, scale=scale, mode=mode)
    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fp:
        fp.write(text)


__all__ = ["to_dxf", "export", "METRES_TO_INCHES"]
