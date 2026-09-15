"""E21/HM3: the home-server profile is DB-only and the AWS default render is unchanged."""
import pathlib
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "modelmatch-postgres"


def render(*value_files, sets=()):
    cmd = ["helm", "template", "modelmatch-postgres", str(CHART)]
    for name in value_files:
        cmd.extend(["-f", str(CHART / name)])
    for value in sets:
        cmd.extend(["--set", value])
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)
    return {(obj["kind"], obj["metadata"]["name"]): obj
            for obj in yaml.safe_load_all(result.stdout) if obj}


class AwsDefaultTests(unittest.TestCase):
    """The AWS production render must keep every P13 fact after the profile knobs."""

    def test_default_render_keeps_aws_contract(self):
        docs = render()
        self.assertEqual(set(docs), {("StorageClass", "modelmatch-gp3"),
                                     ("ExternalSecret", "modelmatch-db-app"),
                                     ("Cluster", "modelmatch-postgres"),
                                     ("Job", "modelmatch-postgres-migrate")})
        sc = docs["StorageClass", "modelmatch-gp3"]
        self.assertEqual((sc["provisioner"], sc["parameters"], sc["reclaimPolicy"],
                          sc["allowVolumeExpansion"], sc["volumeBindingMode"]),
                         ("ebs.csi.aws.com", {"type": "gp3"}, "Delete", True, "WaitForFirstConsumer"))
        cluster = docs["Cluster", "modelmatch-postgres"]["spec"]
        self.assertEqual(cluster["instances"], 2)
        self.assertEqual(cluster["storage"], {"storageClass": "modelmatch-gp3", "size": "5Gi"})
        self.assertNotIn("nodeSelector", cluster["affinity"])


class HomeServerProfileTests(unittest.TestCase):
    def setUp(self):
        self.docs = render("values.yaml", "values-home-server.yaml")

    def test_renders_only_storage_and_one_private_instance(self):
        self.assertEqual(set(self.docs), {("StorageClass", "home-server-retain"),
                                          ("Cluster", "modelmatch-postgres")})
        cluster = self.docs["Cluster", "modelmatch-postgres"]
        self.assertEqual(cluster["metadata"]["namespace"], "app")
        spec = cluster["spec"]
        self.assertEqual(spec["instances"], 1)
        self.assertEqual(spec["imageName"], "ghcr.io/cloudnative-pg/postgresql:16.10-system-trixie")
        self.assertFalse(spec["enableSuperuserAccess"])
        self.assertEqual(spec["storage"]["storageClass"], "home-server-retain")
        self.assertEqual(spec["affinity"]["nodeSelector"], {"kubernetes.io/hostname": "driftplain-home"})

    def test_storage_is_local_path_with_retain(self):
        sc = self.docs["StorageClass", "home-server-retain"]
        self.assertEqual(sc["provisioner"], "rancher.io/local-path")
        self.assertEqual(sc["reclaimPolicy"], "Retain")
        self.assertEqual(sc["volumeBindingMode"], "WaitForFirstConsumer")
        self.assertFalse(sc["allowVolumeExpansion"])
        self.assertNotIn("parameters", sc)

    def test_original_credential_and_role_contract_is_preserved(self):
        initdb = self.docs["Cluster", "modelmatch-postgres"]["spec"]["bootstrap"]["initdb"]
        self.assertEqual((initdb["database"], initdb["owner"], initdb["secret"]["name"]),
                         ("modelmatch", "modelmatch", "modelmatch-db-app"))
        self.assertEqual(initdb["postInitApplicationSQL"],
                         ['ALTER ROLE "modelmatch" CREATEROLE', 'ALTER SCHEMA public OWNER TO "modelmatch"'])

    def test_no_hooks_images_or_aws_references(self):
        rendered = str(self.docs)
        for forbidden in ("Job", "ExternalSecret", "alembic", "app.catalog.seed", "app.demo.seed",
                          "dkr.ecr", "957261948820", "ebs.csi", "external-secrets.io",
                          "DEMO_SEED_PASSWORD", "PostSync"):
            self.assertNotIn(forbidden, rendered, forbidden)

    def test_seed_or_migrate_cannot_be_reenabled_by_a_tag_bump_alone(self):
        bumped = render("values.yaml", "values-home-server.yaml", sets=("migrate.image.tag=1.0.24",))
        self.assertEqual(bumped, self.docs)


class HomeApplicationTests(unittest.TestCase):
    def load(self, name):
        return yaml.safe_load((ROOT / "argocd" / "home-server" / "apps" / name).read_text())

    def test_aws_root_does_not_watch_the_home_directory(self):
        aws_apps = {p.name for p in (ROOT / "argocd" / "apps").glob("*.yaml")}
        self.assertNotIn("home-server", str(aws_apps))
        aws_pg = yaml.safe_load((ROOT / "argocd" / "apps" / "modelmatch-postgres.yaml").read_text())
        self.assertNotIn("helm", aws_pg["spec"]["source"])  # AWS render stays values.yaml only

    def test_home_postgres_app_layers_the_profile_on_the_same_chart(self):
        app = self.load("modelmatch-postgres.yaml")
        self.assertEqual(app["spec"]["source"]["path"], "charts/modelmatch-postgres")
        self.assertEqual(app["spec"]["source"]["helm"]["valueFiles"], ["values.yaml", "values-home-server.yaml"])
        self.assertEqual(app["spec"]["destination"]["namespace"], "app")

    def test_home_operator_pin_supports_kubernetes_1_36_without_touching_aws(self):
        home = self.load("cnpg-operator.yaml")
        aws = yaml.safe_load((ROOT / "argocd" / "apps" / "cnpg-operator.yaml").read_text())
        self.assertEqual(home["spec"]["source"]["targetRevision"], "0.29.0")
        self.assertEqual(aws["spec"]["source"]["targetRevision"], "0.28.3")


class HomeRootTests(unittest.TestCase):
    """The home root App-of-Apps watches only the home child directory, from main."""

    def setUp(self):
        self.root = yaml.safe_load((ROOT / "argocd" / "home-server" / "root.yaml").read_text())

    def test_root_watches_the_home_apps_directory_on_main(self):
        self.assertEqual((self.root["kind"], self.root["metadata"]["name"], self.root["metadata"]["namespace"]),
                         ("Application", "home-server-root", "argocd"))
        source = self.root["spec"]["source"]
        self.assertEqual(source["repoURL"], "https://github.com/Steve-droid/driftplain-gitops.git")
        self.assertEqual(source["targetRevision"], "main")
        self.assertEqual(source["path"], "argocd/home-server/apps")
        self.assertEqual(source["directory"], {"recurse": False})
        self.assertEqual(self.root["spec"]["destination"],
                         {"server": "https://kubernetes.default.svc", "namespace": "argocd"})
        self.assertEqual(self.root["spec"]["syncPolicy"]["automated"], {"prune": True, "selfHeal": True})
        self.assertIn("resources-finalizer.argocd.argoproj.io", self.root["metadata"]["finalizers"])

    def test_root_directory_holds_only_the_db_children_for_hm3(self):
        children = sorted(p.name for p in (ROOT / "argocd" / "home-server" / "apps").glob("*.yaml"))
        self.assertEqual(children, ["cnpg-operator.yaml", "modelmatch-postgres.yaml"])
        for name in children:
            app = yaml.safe_load((ROOT / "argocd" / "home-server" / "apps" / name).read_text())
            self.assertEqual(app["spec"]["destination"]["server"], "https://kubernetes.default.svc")
            self.assertTrue(app["spec"]["syncPolicy"]["automated"]["prune"])
            self.assertTrue(app["spec"]["syncPolicy"]["automated"]["selfHeal"])


if __name__ == "__main__":
    unittest.main()
