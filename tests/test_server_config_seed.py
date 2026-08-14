"""Unit tests for web.server.seed_config_dir.

companies.yaml is live per-user data edited from the UI (see
config/companies.yaml.example + .gitignore), so it must be seeded once from
the shipped example and never overwritten again. preferences.yaml is fully
versioned in the image and should keep syncing on every startup.
"""
from web.server import seed_config_dir


def test_seeds_companies_yaml_from_example_when_missing(tmp_path):
    image_dir = tmp_path / "image_config"
    dest_dir = tmp_path / "volume_config"
    image_dir.mkdir()
    dest_dir.mkdir()
    (image_dir / "companies.yaml.example").write_text("companies:\n  - name: Acme\n")

    seed_config_dir(image_dir, str(dest_dir))

    assert (dest_dir / "companies.yaml").read_text() == "companies:\n  - name: Acme\n"


def test_never_overwrites_existing_companies_yaml(tmp_path):
    image_dir = tmp_path / "image_config"
    dest_dir = tmp_path / "volume_config"
    image_dir.mkdir()
    dest_dir.mkdir()
    (image_dir / "companies.yaml.example").write_text("companies:\n  - name: Acme\n")
    (dest_dir / "companies.yaml").write_text("companies:\n  - name: LiveEditedReddit\n")

    seed_config_dir(image_dir, str(dest_dir))

    assert (dest_dir / "companies.yaml").read_text() == "companies:\n  - name: LiveEditedReddit\n"


def test_no_companies_example_leaves_dest_untouched(tmp_path):
    image_dir = tmp_path / "image_config"
    dest_dir = tmp_path / "volume_config"
    image_dir.mkdir()
    dest_dir.mkdir()

    seed_config_dir(image_dir, str(dest_dir))

    assert not (dest_dir / "companies.yaml").exists()


def test_syncs_preferences_yaml_on_every_call(tmp_path):
    image_dir = tmp_path / "image_config"
    dest_dir = tmp_path / "volume_config"
    image_dir.mkdir()
    dest_dir.mkdir()
    (image_dir / "preferences.yaml").write_text("match_threshold: 7\n")
    (dest_dir / "preferences.yaml").write_text("match_threshold: 3\n")

    seed_config_dir(image_dir, str(dest_dir))

    assert (dest_dir / "preferences.yaml").read_text() == "match_threshold: 7\n"


def test_same_path_for_image_and_dest_is_a_noop(tmp_path):
    shared_dir = tmp_path / "config"
    shared_dir.mkdir()
    (shared_dir / "preferences.yaml").write_text("match_threshold: 7\n")

    seed_config_dir(shared_dir, str(shared_dir))

    assert (shared_dir / "preferences.yaml").read_text() == "match_threshold: 7\n"
