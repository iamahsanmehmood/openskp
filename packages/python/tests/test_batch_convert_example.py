"""The batch converter example must skip bad files and keep going."""

import importlib.util
import pathlib
import shutil

root = pathlib.Path(__file__).resolve().parents[3]
path = root / "examples/python/batch_convert.py"
spec = importlib.util.spec_from_file_location("batch_convert", path)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)

SAMPLE = root / "examples/web-viewer/samples/chair_and_table.skp"
ROOT_FACES = pathlib.Path(__file__).resolve().parent / "fixtures" / "arcs_2017.skp"


def test_converts_good_files_and_mirrors_subfolders(tmp_path, capsys):
    source = tmp_path / "in"
    (source / "sub").mkdir(parents=True)
    shutil.copy(SAMPLE, source / "a.skp")
    shutil.copy(SAMPLE, source / "sub" / "b.SKP")
    out = tmp_path / "out"

    assert batch.main([str(source), str(out)]) == 0
    assert (out / "a.glb").read_bytes()[:4] == b"glTF"
    assert not (out / "sub").exists()  # not recursive by default

    assert batch.main([str(source), str(out), "--recursive"]) == 0
    assert (out / "sub" / "b.glb").read_bytes()[:4] == b"glTF"
    output = capsys.readouterr().out
    assert "2 converted, 0 failed" in output
    assert "86 faces" in output


def test_face_count_includes_faces_drawn_directly_in_the_model(tmp_path):
    # chair_and_table.skp keeps its faces in component definitions.
    assert batch.convert_one(SAMPLE, tmp_path / "chair.glb") == 86
    # arcs_2017.skp has faces in model.root and none in definitions.
    assert batch.convert_one(ROOT_FACES, tmp_path / "arcs.glb") == 2


def test_bad_files_are_reported_and_the_run_continues(tmp_path, capsys):
    source = tmp_path / "in"
    source.mkdir()
    (source / "a_junk.skp").write_text("not a sketchup file")
    data = SAMPLE.read_bytes()
    (source / "b_truncated.skp").write_bytes(data[: len(data) // 2])
    shutil.copy(SAMPLE, source / "c_good.skp")
    out = tmp_path / "out"

    assert batch.main([str(source), str(out)]) == 1
    captured = capsys.readouterr()
    assert "FAILED  a_junk.skp" in captured.err
    assert "FAILED  b_truncated.skp" in captured.err
    assert (out / "c_good.glb").exists()
    assert "1 converted, 2 failed" in captured.out
