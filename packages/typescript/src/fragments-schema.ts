import * as flatbuffers from 'flatbuffers';

/**
 * Low-level FlatBuffers serialization helpers matching ThatOpen Fragments binary schema.
 * Zero external runtime dependencies beyond 'flatbuffers'.
 */

export class FloatVector {
  static createFloatVector(builder: flatbuffers.Builder, x: number, y: number, z: number): flatbuffers.Offset {
    builder.prep(4, 12);
    builder.writeFloat32(z);
    builder.writeFloat32(y);
    builder.writeFloat32(x);
    return builder.offset();
  }
}

export class ShellProfile {
  static startShellProfile(builder: flatbuffers.Builder): void {
    builder.startObject(1);
  }

  static addIndices(builder: flatbuffers.Builder, indicesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(0, indicesOffset, 0);
  }

  static createIndicesVector(builder: flatbuffers.Builder, data: number[] | Uint16Array): flatbuffers.Offset {
    builder.startVector(2, data.length, 2);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt16(data[i]);
    }
    return builder.endVector();
  }

  static endShellProfile(builder: flatbuffers.Builder): flatbuffers.Offset {
    const offset = builder.endObject();
    builder.requiredField(offset, 4);
    return offset;
  }

  static createShellProfile(builder: flatbuffers.Builder, indicesOffset: flatbuffers.Offset): flatbuffers.Offset {
    ShellProfile.startShellProfile(builder);
    ShellProfile.addIndices(builder, indicesOffset);
    return ShellProfile.endShellProfile(builder);
  }
}

export class BigShellProfile {
  static startBigShellProfile(builder: flatbuffers.Builder): void {
    builder.startObject(1);
  }

  static addIndices(builder: flatbuffers.Builder, indicesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(0, indicesOffset, 0);
  }

  static createIndicesVector(builder: flatbuffers.Builder, data: number[] | Uint32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static endBigShellProfile(builder: flatbuffers.Builder): flatbuffers.Offset {
    const offset = builder.endObject();
    builder.requiredField(offset, 4);
    return offset;
  }

  static createBigShellProfile(builder: flatbuffers.Builder, indicesOffset: flatbuffers.Offset): flatbuffers.Offset {
    BigShellProfile.startBigShellProfile(builder);
    BigShellProfile.addIndices(builder, indicesOffset);
    return BigShellProfile.endBigShellProfile(builder);
  }
}

export enum ShellType {
  NONE = 0,
  BIG = 1,
}

export class Shell {
  static startShell(builder: flatbuffers.Builder): void {
    builder.startObject(7);
  }

  static addProfiles(builder: flatbuffers.Builder, profilesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(0, profilesOffset, 0);
  }

  static createProfilesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addHoles(builder: flatbuffers.Builder, holesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(1, holesOffset, 0);
  }

  static createHolesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addPoints(builder: flatbuffers.Builder, pointsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(2, pointsOffset, 0);
  }

  static startPointsVector(builder: flatbuffers.Builder, numElems: number): void {
    builder.startVector(12, numElems, 4);
  }

  static addBigProfiles(builder: flatbuffers.Builder, bigProfilesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(3, bigProfilesOffset, 0);
  }

  static createBigProfilesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addBigHoles(builder: flatbuffers.Builder, bigHolesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(4, bigHolesOffset, 0);
  }

  static createBigHolesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addType(builder: flatbuffers.Builder, type: ShellType): void {
    builder.addFieldInt8(5, type, ShellType.NONE);
  }

  static addProfilesFaceIds(builder: flatbuffers.Builder, profilesFaceIdsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(6, profilesFaceIdsOffset, 0);
  }

  static createProfilesFaceIdsVector(builder: flatbuffers.Builder, data: number[] | Int16Array): flatbuffers.Offset {
    builder.startVector(2, data.length, 2);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt16(data[i]);
    }
    return builder.endVector();
  }

  static endShell(builder: flatbuffers.Builder): flatbuffers.Offset {
    const offset = builder.endObject();
    builder.requiredField(offset, 4); // profiles
    builder.requiredField(offset, 6); // holes
    builder.requiredField(offset, 8); // points
    return offset;
  }
}

export enum RepresentationClass {
  NONE = 0,
  SHELL = 1,
  CIRCLE_EXTRUSION = 2,
}

export class Representation {
  static createRepresentation(
    builder: flatbuffers.Builder,
    id: number,
    bbox_min_x: number,
    bbox_min_y: number,
    bbox_min_z: number,
    bbox_max_x: number,
    bbox_max_y: number,
    bbox_max_z: number,
    representation_class: number = RepresentationClass.SHELL
  ): flatbuffers.Offset {
    builder.prep(4, 32);
    builder.pad(3);
    builder.writeInt8(representation_class);
    builder.prep(4, 24);
    builder.prep(4, 12);
    builder.writeFloat32(bbox_max_z);
    builder.writeFloat32(bbox_max_y);
    builder.writeFloat32(bbox_max_x);
    builder.prep(4, 12);
    builder.writeFloat32(bbox_min_z);
    builder.writeFloat32(bbox_min_y);
    builder.writeFloat32(bbox_min_x);
    builder.writeInt32(id);
    return builder.offset();
  }
}

export class Material {
  static createMaterial(
    builder: flatbuffers.Builder,
    r: number,
    g: number,
    b: number,
    a: number,
    rendered_faces: number = 2,
    stroke: number = 0
  ): flatbuffers.Offset {
    builder.prep(1, 6);
    builder.writeInt8(stroke);
    builder.writeInt8(rendered_faces);
    builder.writeInt8(a);
    builder.writeInt8(b);
    builder.writeInt8(g);
    builder.writeInt8(r);
    return builder.offset();
  }
}

export class Transform {
  static createTransform(
    builder: flatbuffers.Builder,
    position_x: number,
    position_y: number,
    position_z: number,
    x_direction_x: number,
    x_direction_y: number,
    x_direction_z: number,
    y_direction_x: number,
    y_direction_y: number,
    y_direction_z: number
  ): flatbuffers.Offset {
    builder.prep(8, 48);
    builder.prep(4, 12);
    builder.writeFloat32(y_direction_z);
    builder.writeFloat32(y_direction_y);
    builder.writeFloat32(y_direction_x);
    builder.prep(4, 12);
    builder.writeFloat32(x_direction_z);
    builder.writeFloat32(x_direction_y);
    builder.writeFloat32(x_direction_x);
    builder.prep(8, 24);
    builder.writeFloat64(position_z);
    builder.writeFloat64(position_y);
    builder.writeFloat64(position_x);
    return builder.offset();
  }
}

export class Sample {
  static createSample(
    builder: flatbuffers.Builder,
    item: number,
    material: number,
    representation: number,
    local_transform: number = 0
  ): flatbuffers.Offset {
    builder.prep(4, 16);
    builder.writeInt32(local_transform);
    builder.writeInt32(representation);
    builder.writeInt32(material);
    builder.writeInt32(item);
    return builder.offset();
  }
}

export class SpatialStructure {
  static startSpatialStructure(builder: flatbuffers.Builder): void {
    builder.startObject(3);
  }

  static addLocalId(builder: flatbuffers.Builder, localId: number): void {
    builder.addFieldInt32(0, localId, 0);
  }

  static addCategory(builder: flatbuffers.Builder, categoryOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(1, categoryOffset, 0);
  }

  static addChildren(builder: flatbuffers.Builder, childrenOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(2, childrenOffset, 0);
  }

  static createChildrenVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static endSpatialStructure(builder: flatbuffers.Builder): flatbuffers.Offset {
    return builder.endObject();
  }
}

export class Meshes {
  static startMeshes(builder: flatbuffers.Builder): void {
    builder.startObject(14);
  }

  static addCoordinates(builder: flatbuffers.Builder, coordinatesOffset: flatbuffers.Offset): void {
    builder.addFieldStruct(0, coordinatesOffset, 0);
  }

  static addMeshesItems(builder: flatbuffers.Builder, meshesItemsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(1, meshesItemsOffset, 0);
  }

  static createMeshesItemsVector(builder: flatbuffers.Builder, data: number[] | Int32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addSamples(builder: flatbuffers.Builder, samplesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(2, samplesOffset, 0);
  }

  static startSamplesVector(builder: flatbuffers.Builder, numElems: number): void {
    builder.startVector(16, numElems, 4);
  }

  static addRepresentations(builder: flatbuffers.Builder, representationsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(3, representationsOffset, 0);
  }

  static startRepresentationsVector(builder: flatbuffers.Builder, numElems: number): void {
    builder.startVector(32, numElems, 4);
  }

  static addMaterials(builder: flatbuffers.Builder, materialsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(4, materialsOffset, 0);
  }

  static startMaterialsVector(builder: flatbuffers.Builder, numElems: number): void {
    builder.startVector(6, numElems, 1);
  }

  static addCircleExtrusions(builder: flatbuffers.Builder, circleExtrusionsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(5, circleExtrusionsOffset, 0);
  }

  static createCircleExtrusionsVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addShells(builder: flatbuffers.Builder, shellsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(6, shellsOffset, 0);
  }

  static createShellsVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static startShellsVector(builder: flatbuffers.Builder, numElems: number): void {
    builder.startVector(4, numElems, 4);
  }

  static addLocalTransforms(builder: flatbuffers.Builder, localTransformsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(7, localTransformsOffset, 0);
  }

  static startLocalTransformsVector(builder: flatbuffers.Builder, numElems: number): void {
    builder.startVector(48, numElems, 8);
  }

  static addGlobalTransforms(builder: flatbuffers.Builder, globalTransformsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(8, globalTransformsOffset, 0);
  }

  static startGlobalTransformsVector(builder: flatbuffers.Builder, numElems: number): void {
    builder.startVector(48, numElems, 8);
  }

  static addMaterialIds(builder: flatbuffers.Builder, materialIdsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(9, materialIdsOffset, 0);
  }

  static createMaterialIdsVector(builder: flatbuffers.Builder, data: number[] | Int32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addRepresentationIds(builder: flatbuffers.Builder, representationIdsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(10, representationIdsOffset, 0);
  }

  static createRepresentationIdsVector(builder: flatbuffers.Builder, data: number[] | Int32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addSampleIds(builder: flatbuffers.Builder, sampleIdsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(11, sampleIdsOffset, 0);
  }

  static createSampleIdsVector(builder: flatbuffers.Builder, data: number[] | Int32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addLocalTransformIds(builder: flatbuffers.Builder, localTransformIdsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(12, localTransformIdsOffset, 0);
  }

  static createLocalTransformIdsVector(builder: flatbuffers.Builder, data: number[] | Int32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addGlobalTransformIds(builder: flatbuffers.Builder, globalTransformIdsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(13, globalTransformIdsOffset, 0);
  }

  static createGlobalTransformIdsVector(builder: flatbuffers.Builder, data: number[] | Int32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static endMeshes(builder: flatbuffers.Builder): flatbuffers.Offset {
    const offset = builder.endObject();
    builder.requiredField(offset, 4);
    builder.requiredField(offset, 6);
    builder.requiredField(offset, 8);
    builder.requiredField(offset, 10);
    builder.requiredField(offset, 12);
    builder.requiredField(offset, 14);
    builder.requiredField(offset, 16);
    builder.requiredField(offset, 18);
    builder.requiredField(offset, 20);
    return offset;
  }
}

export class Model {
  static startModel(builder: flatbuffers.Builder): void {
    builder.startObject(15);
  }

  static addMetadata(builder: flatbuffers.Builder, metadataOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(0, metadataOffset, 0);
  }

  static addGuids(builder: flatbuffers.Builder, guidsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(1, guidsOffset, 0);
  }

  static createGuidsVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addGuidsItems(builder: flatbuffers.Builder, guidsItemsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(2, guidsItemsOffset, 0);
  }

  static createGuidsItemsVector(builder: flatbuffers.Builder, data: number[] | Uint32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addMaxLocalId(builder: flatbuffers.Builder, maxLocalId: number): void {
    builder.addFieldInt32(3, maxLocalId, 0);
  }

  static addLocalIds(builder: flatbuffers.Builder, localIdsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(4, localIdsOffset, 0);
  }

  static createLocalIdsVector(builder: flatbuffers.Builder, data: number[] | Uint32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addCategories(builder: flatbuffers.Builder, categoriesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(5, categoriesOffset, 0);
  }

  static createCategoriesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addMeshes(builder: flatbuffers.Builder, meshesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(6, meshesOffset, 0);
  }

  static addAttributes(builder: flatbuffers.Builder, attributesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(7, attributesOffset, 0);
  }

  static createAttributesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addRelations(builder: flatbuffers.Builder, relationsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(8, relationsOffset, 0);
  }

  static createRelationsVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addRelationsItems(builder: flatbuffers.Builder, relationsItemsOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(9, relationsItemsOffset, 0);
  }

  static createRelationsItemsVector(builder: flatbuffers.Builder, data: number[] | Int32Array): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }

  static addGuid(builder: flatbuffers.Builder, guidOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(10, guidOffset, 0);
  }

  static addSpatialStructure(builder: flatbuffers.Builder, spatialStructureOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(11, spatialStructureOffset, 0);
  }

  static addUniqueAttributes(builder: flatbuffers.Builder, uniqueAttributesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(12, uniqueAttributesOffset, 0);
  }

  static createUniqueAttributesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addRelationNames(builder: flatbuffers.Builder, relationNamesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(13, relationNamesOffset, 0);
  }

  static createRelationNamesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static addIndexes(builder: flatbuffers.Builder, indexesOffset: flatbuffers.Offset): void {
    builder.addFieldOffset(14, indexesOffset, 0);
  }

  static createIndexesVector(builder: flatbuffers.Builder, data: flatbuffers.Offset[]): flatbuffers.Offset {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }

  static endModel(builder: flatbuffers.Builder): flatbuffers.Offset {
    const offset = builder.endObject();
    builder.requiredField(offset, 6);
    builder.requiredField(offset, 8);
    builder.requiredField(offset, 12);
    builder.requiredField(offset, 14);
    builder.requiredField(offset, 16);
    builder.requiredField(offset, 24);
    return offset;
  }

  static finishModelBuffer(builder: flatbuffers.Builder, offset: flatbuffers.Offset): void {
    builder.finish(offset, '0001');
  }
}
