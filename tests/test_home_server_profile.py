"""E21 home-server profile contracts: the AWS renders are unchanged, the home renders carry
no EKS/IRSA/ESO/EBS/NLB assumptions, and the home root reconciles exactly the reviewed
children (HM3 database; HM4 platform + app). Requires helm + PyYAML."""
import pathlib
import re
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "modelmatch-postgres"
UMBRELLA = ROOT / "charts" / "modelmatch"
ISSUERS = ROOT / "charts" / "cluster-issuers"
HOME_APPS = ROOT / "argocd" / "home-server" / "apps"
AWS_APPS = ROOT / "argocd" / "apps"
GHCR = "ghcr.io/steve-droid"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
HOME_APP_HOST = "app.home-server.driftplain.dev"
HOME_API_HOST = "api.home-server.driftplain.dev"
AWS_ONLY = ("dkr.ecr", "eks.amazonaws.com/role-arn", "ebs.csi", "external-secrets.io",
            "aws-load-balancer", "sslip.io", "letsencrypt", "modicum.cloud", "driftplain.dev\"")


def render(chart, *value_files, sets=(), namespace=None, release=None):
    cmd = ["helm", "template", release or chart.name, str(chart)]
    if namespace:
        cmd.extend(["--namespace", namespace])
    for name in value_files:
        cmd.extend(["-f", str(chart / name)])
    for value in sets:
        cmd.extend(["--set", value])
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return {(obj["kind"], obj["metadata"]["name"]): obj
            for obj in yaml.safe_load_all(result.stdout) if obj}


def load(path):
    return yaml.safe_load(path.read_text())


def containers(docs, name):
    return docs["Deployment", name]["spec"]["template"]["spec"]["containers"]


# ── HM3: the shared Postgres chart ───────────────────────────────────────────────

class AwsPostgresDefaultTests(unittest.TestCase):
    """The AWS production render must keep every P13 fact after the profile knobs."""

    def test_default_render_keeps_aws_contract(self):
        docs = render(CHART)
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


class HomePostgresProfileTests(unittest.TestCase):
    def setUp(self):
        self.docs = render(CHART, "values.yaml", "values-home-server.yaml")

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
        bumped = render(CHART, "values.yaml", "values-home-server.yaml", sets=("migrate.image.tag=1.0.24",))
        self.assertEqual(bumped, self.docs)


# ── HM4: the umbrella (FE + BE + Ingress) ────────────────────────────────────────

class AwsUmbrellaDefaultTests(unittest.TestCase):
    """The committed AWS values keep ECR-by-tag images and the IRSA annotation."""

    def setUp(self):
        self.docs = render(UMBRELLA, namespace="app", release="modelmatch")

    def test_images_are_ecr_by_tag(self):
        for name in ("modelmatch-backend", "modelmatch-frontend"):
            image = containers(self.docs, name)[0]["image"]
            self.assertTrue(image.startswith("957261948820.dkr.ecr.ap-south-1.amazonaws.com/"), image)
            self.assertIn(":1.0.24", image)
            self.assertNotIn("@sha256", image)

    def test_backend_service_account_keeps_irsa(self):
        sa = self.docs["ServiceAccount", "modelmatch-backend"]
        self.assertEqual(sa["metadata"]["annotations"]["eks.amazonaws.com/role-arn"],
                         "arn:aws:iam::957261948820:role/modelmatch-backend-irsa")

    def test_backend_config_is_bedrock(self):
        config = self.docs["ConfigMap", "modelmatch-backend-config"]["data"]
        self.assertEqual(config["LLM_CLIENT"], "bedrock")
        self.assertNotIn("BLOB_STORE", config)


class HomeUmbrellaKnobTests(unittest.TestCase):
    """The profile knobs alone (no home values file yet) give digest refs and no IRSA."""

    ZERO = "sha256:" + "0" * 64

    def setUp(self):
        self.docs = render(UMBRELLA, namespace="app", release="modelmatch", sets=(
            f"backend.image.registry={GHCR}", f"backend.image.digest={self.ZERO}",
            "backend.serviceAccount.irsaRoleName=",
            f"frontend.image.registry={GHCR}", f"frontend.image.digest={self.ZERO}"))

    def test_digest_wins_over_tag_and_registry_override_replaces_ecr(self):
        self.assertEqual(containers(self.docs, "modelmatch-backend")[0]["image"],
                         f"{GHCR}/modelmatch-backend@{self.ZERO}")
        self.assertEqual(containers(self.docs, "modelmatch-frontend")[0]["image"],
                         f"{GHCR}/modelmatch-frontend@{self.ZERO}")
        # Only the workload images are knob-driven; the AGENT_IMAGE ConfigMap literals are
        # profile values (values-home-server.yaml), not template knobs.
        for name in ("modelmatch-backend", "modelmatch-frontend"):
            self.assertNotIn("dkr.ecr", containers(self.docs, name)[0]["image"])

    def test_empty_irsa_role_omits_the_annotation_but_keeps_the_pinned_name(self):
        sa = self.docs["ServiceAccount", "modelmatch-backend"]
        self.assertNotIn("annotations", sa["metadata"])
        self.assertEqual(self.docs["Deployment", "modelmatch-backend"]["spec"]["template"]["spec"]
                         ["serviceAccountName"], "modelmatch-backend")

    def test_extra_annotations_still_render_without_irsa(self):
        docs = render(UMBRELLA, namespace="app", release="modelmatch", sets=(
            "backend.serviceAccount.irsaRoleName=", "backend.serviceAccount.annotations.example=yes"))
        self.assertEqual(docs["ServiceAccount", "modelmatch-backend"]["metadata"]["annotations"],
                         {"example": "yes"})


@unittest.skipUnless((UMBRELLA / "values-home-server.yaml").exists(), "home umbrella profile lands with the app child")
class HomeUmbrellaProfileTests(unittest.TestCase):
    """charts/modelmatch/values-home-server.yaml — the isolated-validation app profile."""

    def setUp(self):
        self.docs = render(UMBRELLA, "values.yaml", "values-home-server.yaml", namespace="app", release="modelmatch")

    def test_images_are_public_ghcr_pinned_by_digest(self):
        for name in ("modelmatch-backend", "modelmatch-frontend"):
            image = containers(self.docs, name)[0]["image"]
            registry_repo, _, digest = image.partition("@")
            self.assertEqual(registry_repo, f"{GHCR}/{name}")
            self.assertRegex(digest, DIGEST)

    def test_no_aws_only_pieces(self):
        rendered = str(self.docs)
        for forbidden in AWS_ONLY:
            self.assertNotIn(forbidden, rendered, forbidden)
        self.assertNotIn("annotations", self.docs["ServiceAccount", "modelmatch-backend"]["metadata"])

    def test_isolated_validation_config(self):
        config = self.docs["ConfigMap", "modelmatch-backend-config"]["data"]
        self.assertEqual(config["LLM_CLIENT"], "fake")
        self.assertEqual(config["BLOB_STORE"], "fake")
        self.assertEqual(config["PUBLIC_BASE_URL"], f"https://{HOME_API_HOST}")
        self.assertEqual(config["CORS_ALLOW_ORIGINS"], f"https://{HOME_APP_HOST}")
        self.assertEqual(config["DATABASE_URL"],
                         "postgresql+psycopg://modelmatch@modelmatch-postgres-rw:5432/modelmatch")
        self.assertEqual(config["CHAT_READONLY_DB_USER"], "modelmatch_chat_ro")
        for key in ("AGENT_IMAGE", "AGENT_SECURITY_IMAGE"):
            registry_repo, _, digest = config[key].partition("@")
            self.assertTrue(registry_repo.startswith(GHCR + "/modelmatch-agent"), config[key])
            self.assertRegex(digest, DIGEST)
        frontend = self.docs["ConfigMap", "modelmatch-frontend-config"]["data"]
        self.assertEqual(frontend["API_BASE_URL"], f"https://{HOME_API_HOST}")

    def test_private_hosts_only_with_the_home_ca_issuer(self):
        ingresses = {name: obj for (kind, name), obj in self.docs.items() if kind == "Ingress"}
        self.assertEqual(set(ingresses), {"modelmatch-app-branded", "modelmatch-app-branded-routes",
                                          "modelmatch-api-branded", "modelmatch-api-branded-routes",
                                          "modelmatch-api-branded-auth"})
        hosts = {rule["host"] for obj in ingresses.values() for rule in obj["spec"]["rules"]}
        self.assertEqual(hosts, {HOME_APP_HOST, HOME_API_HOST})
        for master in ("modelmatch-app-branded", "modelmatch-api-branded"):
            annotations = ingresses[master]["metadata"]["annotations"]
            self.assertEqual(annotations["cert-manager.io/cluster-issuer"], "home-server-ca")
            self.assertEqual(ingresses[master]["spec"]["ingressClassName"], "nginx")

    def test_secret_env_and_limits_are_unchanged(self):
        backend = containers(self.docs, "modelmatch-backend")[0]
        refs = {env["name"]: env["valueFrom"]["secretKeyRef"] for env in backend["env"]}
        self.assertEqual({k: (v["name"], v["key"]) for k, v in refs.items()}, {
            "PGPASSWORD": ("modelmatch-app-secrets", "POSTGRES_PASSWORD"),
            "JWT_SECRET": ("modelmatch-app-secrets", "JWT_SECRET"),
            "CHAT_READONLY_DB_PASSWORD": ("modelmatch-app-secrets", "CHAT_READONLY_DB_PASSWORD")})
        self.assertEqual(backend["resources"]["limits"], {"cpu": "1", "memory": "1Gi"})
        self.assertEqual(containers(self.docs, "modelmatch-frontend")[0]["resources"]["limits"],
                         {"cpu": "250m", "memory": "128Mi"})


# ── HM4: cluster-issuers ─────────────────────────────────────────────────────────

class ClusterIssuerProfileTests(unittest.TestCase):
    def test_aws_default_is_the_two_acme_issuers_only(self):
        docs = render(ISSUERS)
        self.assertEqual(set(docs), {("ClusterIssuer", "letsencrypt-staging"), ("ClusterIssuer", "letsencrypt-prod")})

    def test_home_profile_is_a_private_ca_chain_without_acme(self):
        docs = render(ISSUERS, "values.yaml", "values-home-server.yaml")
        self.assertEqual(set(docs), {("ClusterIssuer", "home-server-selfsigned-bootstrap"),
                                     ("Certificate", "home-server-ca"),
                                     ("ClusterIssuer", "home-server-ca")})
        self.assertEqual(docs["ClusterIssuer", "home-server-selfsigned-bootstrap"]["spec"], {"selfSigned": {}})
        ca = docs["Certificate", "home-server-ca"]
        self.assertEqual(ca["metadata"]["namespace"], "cert-manager")
        self.assertTrue(ca["spec"]["isCA"])
        self.assertEqual(ca["spec"]["issuerRef"]["name"], "home-server-selfsigned-bootstrap")
        self.assertEqual(docs["ClusterIssuer", "home-server-ca"]["spec"]["ca"]["secretName"], ca["spec"]["secretName"])
        self.assertNotIn("acme", str(docs))


# ── The home root and its children ───────────────────────────────────────────────

class HomeApplicationTests(unittest.TestCase):
    def load(self, name):
        return load(HOME_APPS / name)

    def test_aws_root_does_not_watch_the_home_directory(self):
        aws_apps = {p.name for p in AWS_APPS.glob("*.yaml")}
        self.assertNotIn("home-server", str(aws_apps))
        self.assertNotIn("sealed-secrets.yaml", aws_apps)
        aws_pg = load(AWS_APPS / "modelmatch-postgres.yaml")
        self.assertNotIn("helm", aws_pg["spec"]["source"])  # AWS render stays values.yaml only
        self.assertNotIn("helm", load(AWS_APPS / "cluster-issuers.yaml")["spec"]["source"])

    def test_home_postgres_app_layers_the_profile_on_the_same_chart(self):
        app = self.load("modelmatch-postgres.yaml")
        self.assertEqual(app["spec"]["source"]["path"], "charts/modelmatch-postgres")
        self.assertEqual(app["spec"]["source"]["helm"]["valueFiles"], ["values.yaml", "values-home-server.yaml"])
        self.assertEqual(app["spec"]["destination"]["namespace"], "app")

    def test_home_operator_pin_supports_kubernetes_1_36_without_touching_aws(self):
        home = self.load("cnpg-operator.yaml")
        aws = load(AWS_APPS / "cnpg-operator.yaml")
        self.assertEqual(home["spec"]["source"]["targetRevision"], "0.29.0")
        self.assertEqual(aws["spec"]["source"]["targetRevision"], "0.28.3")

    def test_home_ingress_controller_is_private_and_free_of_aws_annotations(self):
        home = self.load("nginx-ingress.yaml")
        aws = load(AWS_APPS / "nginx-ingress.yaml")
        self.assertEqual(home["spec"]["source"]["targetRevision"], aws["spec"]["source"]["targetRevision"])
        values = yaml.safe_load(home["spec"]["source"]["helm"]["values"])
        self.assertEqual(values["controller"]["service"], {"type": "ClusterIP"})
        self.assertFalse(values["controller"]["enableCustomResources"])
        self.assertTrue(home["spec"]["source"]["helm"]["skipCrds"])
        self.assertNotIn("ignoreDifferences", home["spec"])
        self.assertNotIn("aws-load-balancer", str(home))
        self.assertIn("limits", values["controller"]["resources"])

    def test_home_cert_manager_keeps_the_aws_pin_and_server_side_apply(self):
        home = self.load("cert-manager.yaml")
        aws = load(AWS_APPS / "cert-manager.yaml")
        self.assertEqual(home["spec"]["source"]["targetRevision"], aws["spec"]["source"]["targetRevision"])
        self.assertEqual(yaml.safe_load(home["spec"]["source"]["helm"]["values"]),
                         yaml.safe_load(aws["spec"]["source"]["helm"]["values"]))
        self.assertIn("ServerSideApply=true", home["spec"]["syncPolicy"]["syncOptions"])

    def test_home_cluster_issuers_layer_the_private_ca_profile(self):
        home = self.load("cluster-issuers.yaml")
        self.assertEqual(home["spec"]["source"]["path"], "charts/cluster-issuers")
        self.assertEqual(home["spec"]["source"]["helm"]["valueFiles"], ["values.yaml", "values-home-server.yaml"])
        self.assertEqual(home["spec"]["destination"]["namespace"], "cert-manager")

    def test_home_sealed_secrets_controller_is_pinned_and_namespaced(self):
        home = self.load("sealed-secrets.yaml")
        self.assertEqual((home["spec"]["source"]["repoURL"], home["spec"]["source"]["chart"],
                          home["spec"]["source"]["targetRevision"]),
                         ("https://bitnami.github.io/sealed-secrets", "sealed-secrets", "2.20.0"))
        values = yaml.safe_load(home["spec"]["source"]["helm"]["values"])
        self.assertEqual(values["fullnameOverride"], "sealed-secrets-controller")
        self.assertIn("limits", values["resources"])
        self.assertEqual(home["spec"]["destination"]["namespace"], "sealed-secrets")

    def test_home_sync_waves_order_controllers_before_consumers(self):
        def wave(name):
            return int(self.load(name)["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave", "0"))
        self.assertLess(wave("sealed-secrets.yaml"), wave("cert-manager.yaml"))
        self.assertLess(wave("cert-manager.yaml"), wave("nginx-ingress.yaml"))
        self.assertLess(wave("nginx-ingress.yaml"), wave("cluster-issuers.yaml"))
        self.assertLess(wave("cnpg-operator.yaml"), wave("modelmatch-postgres.yaml"))
        if (HOME_APPS / "modelmatch.yaml").exists():
            self.assertLess(wave("cluster-issuers.yaml"), wave("modelmatch.yaml"))
            self.assertLess(wave("modelmatch-postgres.yaml"), wave("modelmatch.yaml"))
        if (HOME_APPS / "app-secrets.yaml").exists():
            self.assertLess(wave("sealed-secrets.yaml"), wave("app-secrets.yaml"))
            self.assertLess(wave("app-secrets.yaml"), wave("modelmatch-postgres.yaml"))


class HomeSealedManifestTests(unittest.TestCase):
    """argocd/home-server/sealed: exactly the two app Secrets, sealed (strict scope), no plaintext."""

    SEALED = ROOT / "argocd" / "home-server" / "sealed"
    EXPECTED = {"modelmatch-app-secrets": ("Opaque", {"JWT_SECRET", "POSTGRES_PASSWORD",
                                                       "CHAT_READONLY_DB_PASSWORD", "DEMO_SEED_PASSWORD"}, {}),
                "modelmatch-db-app": ("kubernetes.io/basic-auth", {"username", "password"}, {"cnpg.io/reload": "true"})}

    def setUp(self):
        self.docs = {p.stem: load(p) for p in self.SEALED.glob("*.yaml")}

    def test_exactly_the_two_sealed_app_secrets(self):
        self.assertEqual(set(self.docs), set(self.EXPECTED))
        for name, (kind, keys, labels) in self.EXPECTED.items():
            doc = self.docs[name]
            self.assertEqual((doc["apiVersion"], doc["kind"]), ("bitnami.com/v1alpha1", "SealedSecret"))
            self.assertEqual((doc["metadata"]["name"], doc["metadata"]["namespace"]), (name, "app"))
            self.assertEqual(set(doc["spec"]["encryptedData"]), keys)
            template = doc["spec"]["template"]
            self.assertEqual(template["type"], kind)
            self.assertEqual((template["metadata"]["name"], template["metadata"]["namespace"]), (name, "app"))
            self.assertEqual(template["metadata"].get("labels", {}), labels)
            # strict scope = no cluster-wide / namespace-wide annotation
            self.assertFalse({k for k in (doc["metadata"].get("annotations") or {}) if "sealedsecrets" in k})
            for value in doc["spec"]["encryptedData"].values():
                self.assertRegex(value, r"^Ag[A-Za-z0-9+/=]{200,}$")  # RSA-OAEP session key + AES-GCM payload

    def test_sealed_directory_is_the_app_secrets_child_source(self):
        app = load(HOME_APPS / "app-secrets.yaml")
        self.assertEqual(app["spec"]["source"]["path"], "argocd/home-server/sealed")
        self.assertEqual(app["spec"]["source"]["directory"], {"recurse": False})
        self.assertEqual(app["spec"]["destination"]["namespace"], "app")
        self.assertNotIn("CreateNamespace=true", app["spec"].get("syncPolicy", {}).get("syncOptions", []))


class HomeRootTests(unittest.TestCase):
    """The home root App-of-Apps watches only the home child directory, from main."""

    HM3_CHILDREN = ["cnpg-operator.yaml", "modelmatch-postgres.yaml"]
    HM4_PLATFORM = ["cert-manager.yaml", "cluster-issuers.yaml", "nginx-ingress.yaml", "sealed-secrets.yaml"]
    HM4_APP = ["app-secrets.yaml", "modelmatch.yaml"]

    def setUp(self):
        self.root = load(ROOT / "argocd" / "home-server" / "root.yaml")

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

    def test_root_directory_holds_only_reviewed_children(self):
        children = sorted(p.name for p in HOME_APPS.glob("*.yaml"))
        allowed = set(self.HM3_CHILDREN + self.HM4_PLATFORM + self.HM4_APP)
        self.assertTrue(set(children) <= allowed, children)
        self.assertTrue(set(self.HM3_CHILDREN + self.HM4_PLATFORM) <= set(children), children)
        for name in children:
            app = load(HOME_APPS / name)
            self.assertEqual(app["spec"]["destination"]["server"], "https://kubernetes.default.svc")
            self.assertTrue(app["spec"]["syncPolicy"]["automated"]["prune"], name)
            self.assertTrue(app["spec"]["syncPolicy"]["automated"]["selfHeal"], name)
            self.assertIn("resources-finalizer.argocd.argoproj.io", app["metadata"]["finalizers"], name)
            source = app["spec"]["source"]
            if "path" in source:
                self.assertEqual(source["targetRevision"], "main", name)


if __name__ == "__main__":
    unittest.main()
