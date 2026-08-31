from typer.testing import CliRunner

from segregator.cli import app

runner = CliRunner()


def test_init_creates_db_and_tree(isolated_project):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    archive_dir = isolated_project["archive_dir"]
    assert (archive_dir / "segregator.db").exists()
    assert (archive_dir / "archiwum" / "wg-daty-dokumentu").is_dir()
    assert (archive_dir / "archiwum" / "wg-daty-platnosci" / "_bez-daty-platnosci").is_dir()
    assert (archive_dir / "blobs").is_dir()
    assert (archive_dir / "rejestry").is_dir()
    assert "создано новых директорий" in result.output


def test_init_twice_is_noop(isolated_project):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert "применено новых миграций — 0" in result.output
    assert "создано новых директорий — 0" in result.output
