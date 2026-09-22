"""E21 home-server profile contracts: the AWS renders are unchanged, the home renders carry
no EKS/IRSA/ESO/EBS/NLB assumptions, and the home root reconciles exactly the reviewed
children (HM3 database; HM4 platform + app; HM5 monitoring, heartbeat, gated backup).
Requires helm + PyYAML."""
import copy
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
HEARTBEAT = ROOT / "charts" / "home-server-heartbeat"
BACKUP = ROOT / "charts" / "home-server-backup"
CLOUDFLARED = ROOT / "charts" / "home-server-cloudflared"
AWS_APPS = ROOT / "argocd" / "apps"
GHCR = "ghcr.io/steve-droid"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
HOME_APP_HOST = "app.home-server.driftplain.dev"
HOME_API_HOST = "api.home-server.driftplain.dev"
STAGING_APP_HOST = "staging.driftplain.dev"
STAGING_API_HOST = "api-staging.driftplain.dev"
RUNTIME_APP_HOST = "driftplain.dev"
RUNTIME_API_HOST = "api.driftplain.dev"
# The runtime pair is served from home since HM7 (September 22, 2026); modicum.cloud never is.
AWS_ONLY = ("dkr.ecr", "eks.amazonaws.com/role-arn", "ebs.csi", "external-secrets.io",
            "aws-load-balancer", "sslip.io", "letsencrypt", "modicum.cloud")


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

    def test_home_turns_on_the_cnpg_pod_monitor_and_aws_does_not(self):
        home = render(CHART, "values.yaml", "values-home-server.yaml")["Cluster", "modelmatch-postgres"]
        self.assertEqual(home["spec"]["monitoring"], {"enablePodMonitor": True})
        aws = render(CHART, "values.yaml")["Cluster", "modelmatch-postgres"]
        self.assertNotIn("monitoring", aws["spec"])

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

    def test_new_image_packages_preserve_resources_and_runtime_configuration(self):
        digest = "sha256:" + "1" * 64
        review = f"{GHCR}/driftplain-agent@{digest}"
        security = f"{GHCR}/driftplain-agent-security@{digest}"
        updated = render(
            UMBRELLA, "values.yaml", "values-home-server.yaml",
            namespace="app", release="modelmatch", sets=(
                "backend.image.repository=driftplain-backend",
                f"backend.image.digest={digest}",
                "frontend.image.repository=driftplain-frontend",
                f"frontend.image.digest={digest}",
                f"backend.config.AGENT_IMAGE={review}",
                f"backend.config.AGENT_SECURITY_IMAGE={security}",
            ),
        )
        expected = copy.deepcopy(self.docs)
        for component in ("backend", "frontend"):
            containers(expected, f"modelmatch-{component}")[0]["image"] = (
                f"{GHCR}/driftplain-{component}@{digest}"
            )
        config = expected["ConfigMap", "modelmatch-backend-config"]["data"]
        config["AGENT_IMAGE"] = review
        config["AGENT_SECURITY_IMAGE"] = security
        # The backend must roll to pick up the changed agent references.
        old_annotations = expected["Deployment", "modelmatch-backend"]["spec"]["template"]["metadata"]["annotations"]
        new_annotations = updated["Deployment", "modelmatch-backend"]["spec"]["template"]["metadata"]["annotations"]
        self.assertNotEqual(old_annotations["checksum/config"], new_annotations["checksum/config"])
        old_annotations["checksum/config"] = new_annotations["checksum/config"]
        self.assertEqual(updated, expected)

    def test_isolated_validation_config(self):
        config = self.docs["ConfigMap", "modelmatch-backend-config"]["data"]
        self.assertEqual(config["LLM_CLIENT"], "fake")
        self.assertEqual(config["BLOB_STORE"], "fake")
        # HM7: the runtime host set is selected; the browser at https://driftplain.dev calls
        # api.driftplain.dev and both public app origins (runtime, staging) pass CORS and the
        # Google sign-in origin check. The private branded origin stays first.
        self.assertEqual(config["PUBLIC_BASE_URL"], f"https://{RUNTIME_API_HOST}")
        self.assertEqual(config["CORS_ALLOW_ORIGINS"],
                         f"https://{HOME_APP_HOST},https://{RUNTIME_APP_HOST},https://{STAGING_APP_HOST}")
        self.assertEqual(config["GOOGLE_CLIENT_ID"],
                         load(UMBRELLA / "values.yaml")["backend"]["config"]["GOOGLE_CLIENT_ID"])
        self.assertEqual(config["DATABASE_URL"],
                         "postgresql+psycopg://modelmatch@modelmatch-postgres-rw:5432/modelmatch")
        self.assertEqual(config["CHAT_READONLY_DB_USER"], "modelmatch_chat_ro")
        for key in ("AGENT_IMAGE", "AGENT_SECURITY_IMAGE"):
            registry_repo, _, digest = config[key].partition("@")
            self.assertTrue(registry_repo.startswith(GHCR + "/modelmatch-agent"), config[key])
            self.assertRegex(digest, DIGEST)
        frontend = self.docs["ConfigMap", "modelmatch-frontend-config"]["data"]
        self.assertEqual(frontend["API_BASE_URL"], f"https://{RUNTIME_API_HOST}")

    def test_runtime_host_set_is_the_selected_one_and_matches_the_aws_pair(self):
        # The same public hostnames AWS served (P38r) now select the home runtime, so the
        # Google client's authorized origins and every bookmark/CI URL stay valid.
        home, aws = load(UMBRELLA / "values-home-server.yaml")["global"], load(UMBRELLA / "values.yaml")["global"]
        self.assertEqual(home["runtimeHostSet"], "driftplain")
        self.assertEqual(home["additionalHosts"]["driftplain"],
                         {"enabled": True, "appHost": RUNTIME_APP_HOST, "apiHost": RUNTIME_API_HOST})
        self.assertEqual({k: aws["additionalHosts"]["driftplain"][k] for k in ("appHost", "apiHost")},
                         {"appHost": RUNTIME_APP_HOST, "apiHost": RUNTIME_API_HOST})
        self.assertTrue(home["additionalHosts"]["staging"]["enabled"])

    def test_private_hosts_only_with_the_home_ca_issuer(self):
        ingresses = {name: obj for (kind, name), obj in self.docs.items() if kind == "Ingress"}
        self.assertEqual(set(ingresses), {"modelmatch-app-branded", "modelmatch-app-branded-routes",
                                          "modelmatch-api-branded", "modelmatch-api-branded-routes",
                                          "modelmatch-api-branded-auth",
                                          "modelmatch-app-driftplain", "modelmatch-app-driftplain-routes",
                                          "modelmatch-api-driftplain", "modelmatch-api-driftplain-routes",
                                          "modelmatch-api-driftplain-auth",
                                          "modelmatch-app-staging", "modelmatch-app-staging-routes",
                                          "modelmatch-api-staging", "modelmatch-api-staging-routes",
                                          "modelmatch-api-staging-auth"})
        hosts = {rule["host"] for obj in ingresses.values() for rule in obj["spec"]["rules"]}
        self.assertEqual(hosts, {HOME_APP_HOST, HOME_API_HOST, RUNTIME_APP_HOST, RUNTIME_API_HOST,
                                 STAGING_APP_HOST, STAGING_API_HOST})
        for master in ("modelmatch-app-branded", "modelmatch-api-branded",
                       "modelmatch-app-driftplain", "modelmatch-api-driftplain",
                       "modelmatch-app-staging", "modelmatch-api-staging"):
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
        self.assertLess(wave("monitoring.yaml"), wave("monitoring-dashboards.yaml"))
        self.assertLess(wave("monitoring.yaml"), wave("heartbeat.yaml"))
        self.assertLess(wave("sealed-secrets.yaml"), wave("backup.yaml"))
        self.assertLess(wave("sealed-secrets.yaml"), wave("cloudflared.yaml"))
        self.assertLess(wave("monitoring.yaml"), wave("cloudflared.yaml"))


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
        # HM5: no all-default directory block — ArgoCD drops it live and the root stays OutOfSync.
        self.assertNotIn("directory", app["spec"]["source"])
        self.assertEqual(app["spec"]["destination"]["namespace"], "app")
        self.assertNotIn("CreateNamespace=true", app["spec"].get("syncPolicy", {}).get("syncOptions", []))


class HomeArgoCDValuesTests(unittest.TestCase):
    """The home argo-cd install values: private server, limits everywhere, home health checks."""

    def setUp(self):
        self.values = load(ROOT / "argocd" / "home-server" / "argocd-values.yaml")

    def test_server_is_private_and_every_component_is_capped(self):
        self.assertEqual(self.values["server"]["service"]["type"], "ClusterIP")
        self.assertFalse(self.values["dex"]["enabled"])
        for component in ("controller", "server", "repoServer", "redis", "applicationSet"):
            self.assertIn("limits", self.values[component]["resources"], component)

    def test_ingress_health_does_not_wait_for_a_load_balancer_address(self):
        cm = self.values["configs"]["cm"]
        lua = cm["resource.customizations.health.networking.k8s.io_Ingress"]
        self.assertIn('hs.status = "Healthy"', lua)
        self.assertNotIn("loadBalancer", lua)
        self.assertIn("resource.customizations.health.argoproj.io_Application", cm)


class HomeRootTests(unittest.TestCase):
    """The home root App-of-Apps watches only the home child directory, from main."""

    HM3_CHILDREN = ["cnpg-operator.yaml", "modelmatch-postgres.yaml"]
    HM4_PLATFORM = ["cert-manager.yaml", "cluster-issuers.yaml", "nginx-ingress.yaml", "sealed-secrets.yaml"]
    HM4_APP = ["app-secrets.yaml", "modelmatch.yaml"]
    HM5_OPERATION = ["monitoring.yaml", "monitoring-dashboards.yaml", "heartbeat.yaml", "backup.yaml",
                     "cloudflared.yaml"]

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
        allowed = set(self.HM3_CHILDREN + self.HM4_PLATFORM + self.HM4_APP + self.HM5_OPERATION)
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


# ── HM5: bounded monitoring, the heartbeat and the gated backup ─────────────────

class HomeMonitoringTests(unittest.TestCase):
    """The home monitoring child mirrors the AWS trim, adds the home rules and scrapes."""

    def setUp(self):
        self.home = load(HOME_APPS / "monitoring.yaml")
        self.aws = load(AWS_APPS / "monitoring.yaml")
        self.values = yaml.safe_load(self.home["spec"]["source"]["helm"]["values"])
        self.aws_values = yaml.safe_load(self.aws["spec"]["source"]["helm"]["values"])

    def test_same_chart_pin_and_trim_as_aws(self):
        for key in ("repoURL", "chart", "targetRevision"):
            self.assertEqual(self.home["spec"]["source"][key], self.aws["spec"]["source"][key])
        for key in ("alertmanager", "windowsMonitoring", "kubeControllerManager", "kubeScheduler",
                    "kubeEtcd", "kubeProxy"):
            self.assertFalse(self.values[key]["enabled"], key)
        self.assertFalse(self.values["prometheusOperator"]["admissionWebhooks"]["enabled"])
        self.assertTrue(self.values["defaultRules"]["disabled"]["PrometheusNotConnectedToAlertmanagers"])
        self.assertFalse(self.values["grafana"]["persistence"]["enabled"])
        self.assertEqual(self.home["spec"]["ignoreDifferences"], self.aws["spec"]["ignoreDifferences"])
        self.assertIn("ServerSideApply=true", self.home["spec"]["syncPolicy"]["syncOptions"])
        self.assertEqual(self.home["spec"]["destination"]["namespace"], "monitoring")
        self.assertNotIn("ingress", self.values["grafana"])

    def test_every_component_is_capped_and_the_tsdb_is_bounded(self):
        spec = self.values["prometheus"]["prometheusSpec"]
        self.assertEqual(spec["retention"], "2d")
        self.assertEqual(spec["retentionSize"], "1GiB")
        for component in (self.values["prometheusOperator"], spec, self.values["grafana"],
                          self.values["grafana"]["sidecar"], self.values["kube-state-metrics"],
                          self.values["prometheus-node-exporter"],
                          self.values["prometheusOperator"]["prometheusConfigReloader"]):
            self.assertIn("limits", component["resources"])
        for key in ("serviceMonitor", "podMonitor", "rule", "probe"):
            self.assertFalse(spec[f"{key}SelectorNilUsesHelmValues"], key)

    def test_home_rules_cover_disk_certificates_database_app_and_node(self):
        groups = self.values["additionalPrometheusRulesMap"]["home-server"]["groups"]
        rules = {r["alert"]: r for g in groups for r in g["rules"]}
        expected = {
            "HomeServerRootDiskFilling": "warning", "HomeServerRootDiskCritical": "critical",
            "HomeServerCertificateExpiringSoon": "warning", "HomeServerCertificateExpiryCritical": "critical",
            "HomeServerDatabaseNotReady": "critical", "HomeServerAppUnavailable": "critical",
            "HomeServerIngressUnavailable": "critical", "HomeServerArgoCDNotHealthy": "warning",
            "HomeServerNodeNotReady": "critical", "HomeServerMemoryPressure": "warning",
        }
        self.assertEqual({name: r["labels"]["severity"] for name, r in rules.items()}, expected)
        self.assertIn("> 70", rules["HomeServerRootDiskFilling"]["expr"])
        self.assertIn("> 85", rules["HomeServerRootDiskCritical"]["expr"])
        self.assertIn("14 * 24 * 3600", rules["HomeServerCertificateExpiringSoon"]["expr"])
        self.assertIn("cnpg_collector_up", rules["HomeServerDatabaseNotReady"]["expr"])
        for rule in rules.values():
            self.assertIn("for", rule)
            self.assertIn("summary", rule["annotations"])

    def test_scrapes_backend_argocd_and_cert_manager(self):
        prom = self.values["prometheus"]
        self.assertEqual([m["name"] for m in prom["additionalServiceMonitors"]], ["modelmatch-backend"])
        self.assertEqual({m["name"] for m in prom["additionalPodMonitors"]},
                         {"argocd-application-controller", "cert-manager"})
        cert = next(m for m in prom["additionalPodMonitors"] if m["name"] == "cert-manager")
        self.assertEqual(cert["selector"]["matchLabels"]["app.kubernetes.io/component"], "controller")
        self.assertEqual(cert["podMetricsEndpoints"][0]["port"], "http-metrics")

    def test_dashboards_child_reuses_the_aws_chart(self):
        home = load(HOME_APPS / "monitoring-dashboards.yaml")
        aws = load(AWS_APPS / "monitoring-dashboards.yaml")
        self.assertEqual(home["spec"]["source"]["path"], aws["spec"]["source"]["path"])
        self.assertEqual(home["spec"]["destination"]["namespace"], "monitoring")

    def test_aws_monitoring_is_untouched(self):
        self.assertNotIn("additionalPrometheusRulesMap", self.aws_values)
        self.assertEqual(len(self.aws_values["prometheus"]["additionalPodMonitors"]), 1)


class HomeHeartbeatChartTests(unittest.TestCase):
    """charts/home-server-heartbeat: suspended by default, pings only on a clean Prometheus answer."""

    def test_committed_default_is_enabled_with_the_sealed_url(self):
        docs = render(HEARTBEAT, namespace="monitoring")
        self.assertEqual(set(docs), {("CronJob", "home-server-heartbeat"), ("ConfigMap", "home-server-heartbeat"),
                                     ("SealedSecret", "home-server-heartbeat")})
        job = docs["CronJob", "home-server-heartbeat"]
        self.assertFalse(job["spec"]["suspend"])
        self.assertEqual(job["spec"]["schedule"], "*/5 * * * *")
        self.assertRegex(docs["SealedSecret", "home-server-heartbeat"]["spec"]["encryptedData"]["url"],
                         r"^Ag[A-Za-z0-9+/=]{500,}$")
        self.assertEqual(job["spec"]["concurrencyPolicy"], "Forbid")
        pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        container = pod["containers"][0]
        self.assertRegex(container["image"], r"^curlimages/curl@sha256:[0-9a-f]{64}$")
        self.assertIn("limits", container["resources"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        env = {e["name"]: e for e in container["env"]}
        self.assertEqual(env["HEARTBEAT_URL"]["valueFrom"]["secretKeyRef"],
                         {"name": "home-server-heartbeat", "key": "url"})
        self.assertNotIn("value", env["HEARTBEAT_URL"])
        self.assertEqual(env["EDGE_PROBES"]["value"],
                         "https://driftplain.dev/|Driftplain https://api.driftplain.dev/healthz|ok "
                         "https://staging.driftplain.dev/|Driftplain https://api-staging.driftplain.dev/healthz|ok")
        self.assertEqual(docs["ConfigMap", "home-server-heartbeat"]["data"]["heartbeat.sh"],
                         (HEARTBEAT / "scripts" / "heartbeat.sh").read_text().rstrip("\n"))

    def test_gated_render_is_suspended_with_no_secret(self):
        docs = render(HEARTBEAT, namespace="monitoring", sets=("enabled=false", "sealed.encryptedUrl="))
        self.assertEqual(set(docs), {("CronJob", "home-server-heartbeat"), ("ConfigMap", "home-server-heartbeat")})
        self.assertTrue(docs["CronJob", "home-server-heartbeat"]["spec"]["suspend"])

    def test_enabling_with_a_sealed_url_renders_the_sealed_secret_and_unsuspends(self):
        docs = render(HEARTBEAT, namespace="monitoring",
                      sets=("enabled=true", "sealed.encryptedUrl=AgBciphertext"))
        self.assertFalse(docs["CronJob", "home-server-heartbeat"]["spec"]["suspend"])
        sealed = docs["SealedSecret", "home-server-heartbeat"]
        self.assertEqual(sealed["spec"]["encryptedData"], {"url": "AgBciphertext"})
        self.assertEqual(sealed["spec"]["template"]["metadata"]["namespace"], "monitoring")

    def run_script(self, answers, tmp, probes="", edge_bodies=()):
        """Run heartbeat.sh with a fake curl: Prometheus answers per `answers`, each edge URL answers
        with its entry from `edge_bodies` (a body, or "FAIL" for a curl failure), and the ping is logged."""
        fake = tmp / "curl"
        edge = "".join(f'  {url}) ' + ('exit 22' if body == "FAIL" else f'printf %s "{body}"; exit 0') + ';;\n'
                       for url, body in edge_bodies)
        fake.write_text("#!/bin/sh\n"
                        "for a in \"$@\"; do case \"$a\" in\n"
                        "  http://heartbeat.invalid/*) echo ping >> \"$FAKE_LOG\"; exit 0;;\n"
                        f"{edge}"
                        "  esac; done\n"
                        "cat \"$FAKE_ANSWER\"\n")
        fake.chmod(0o755)
        answer = tmp / "answer.json"
        answer.write_text(answers)
        env = {"PATH": f"{tmp}:/usr/bin:/bin", "PROMETHEUS_URL": "http://prom.invalid",
               "HEARTBEAT_URL": "http://heartbeat.invalid/ping", "FAKE_LOG": str(tmp / "log"),
               "FAKE_ANSWER": str(answer), "EDGE_PROBES": probes}
        result = subprocess.run(["/bin/sh", str(HEARTBEAT / "scripts" / "heartbeat.sh")],
                                env=env, text=True, capture_output=True)
        pinged = (tmp / "log").exists()
        return result.returncode, result.stdout.strip(), pinged

    def test_script_pings_only_when_no_critical_alert_fires(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            clean = '{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1758100000.1,"0"]}]}}'
            firing = clean.replace('"0"', '"2"')
            ten = clean.replace('"0"', '"10"')
            self.assertEqual(self.run_script(clean, tmp), (0, "heartbeat sent: no critical alert firing", True))
            for answer in (firing, ten):
                (tmp / "log").unlink(missing_ok=True)
                code, out, pinged = self.run_script(answer, tmp)
                self.assertEqual((code, pinged), (1, False), out)
                self.assertIn("critical alert is firing", out)
            (tmp / "log").unlink(missing_ok=True)
            code, out, pinged = self.run_script('{"status":"error"}', tmp)
            self.assertEqual((code, pinged), (1, False), out)
            self.assertNotIn("heartbeat.invalid", out)

    def test_script_pings_only_when_every_edge_probe_answers_with_its_keyword(self):
        import tempfile
        clean = '{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1758100000.1,"0"]}]}}'
        probes = "https://app.invalid/|Driftplain https://api.invalid/healthz|ok"
        app, api = "https://app.invalid/", "https://api.invalid/healthz"
        html, health = "<title>Driftplain</title>", '{"status":"ok"}'
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            self.assertEqual(self.run_script(clean, tmp, probes, [(app, html), (api, health)]),
                             (0, "heartbeat sent: no critical alert firing, edge probes ok", True))
            for bodies, message in (([(app, "FAIL"), (api, health)], "edge probe failed: https://app.invalid/"),
                                    ([(app, html), (api, "FAIL")], "edge probe failed: https://api.invalid/healthz"),
                                    ([(app, "<title>Modicum</title>"), (api, health)], "keyword missing: https://app.invalid/"),
                                    ([(app, html), (api, '{"status":"degraded"}')], "keyword missing: https://api.invalid/healthz")):
                (tmp / "log").unlink(missing_ok=True)
                code, out, pinged = self.run_script(clean, tmp, probes, bodies)
                self.assertEqual((code, pinged), (1, False), out)
                self.assertIn(message, out)
            # A firing alert is checked first: the probes never run and nothing is pinged.
            (tmp / "log").unlink(missing_ok=True)
            code, out, pinged = self.run_script(clean.replace('"0"', '"1"'), tmp, probes, [(app, "FAIL")])
            self.assertEqual((code, pinged, out), (1, False, "heartbeat withheld: a critical alert is firing"))


class HomeBackupChartTests(unittest.TestCase):
    """charts/home-server-backup: gated CronJob, no token, leaf + owner credential mounted only."""

    def test_committed_default_is_enabled_with_a_pinned_digest_and_the_gate_still_suspends(self):
        docs = render(BACKUP, namespace="home-server-backups")
        job = docs["CronJob", "home-server-backup"]
        self.assertFalse(job["spec"]["suspend"])
        image = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["image"]
        self.assertRegex(image, r"^ghcr\.io/steve-droid/home-server-backup@sha256:[0-9a-f]{64}$")
        gated = render(BACKUP, namespace="home-server-backups", sets=("enabled=false",))
        self.assertTrue(gated["CronJob", "home-server-backup"]["spec"]["suspend"])

    def test_render_carries_no_credentials_and_mounts_only_the_leaf_and_owner(self):
        docs = render(BACKUP, namespace="home-server-backups", sets=("enabled=false",))
        self.assertEqual(set(docs), {("CronJob", "home-server-backup"), ("ConfigMap", "home-server-backup"),
                                     ("ServiceAccount", "home-server-backup"),
                                     ("SealedSecret", "home-server-backup-db-owner")})
        sealed = docs["SealedSecret", "home-server-backup-db-owner"]
        self.assertEqual(set(sealed["spec"]["encryptedData"]), {"username", "password"})
        self.assertTrue(all(v.startswith("Ag") and len(v) > 500 for v in sealed["spec"]["encryptedData"].values()))
        self.assertEqual(sealed["spec"]["template"],
                         {"type": "kubernetes.io/basic-auth",
                          "metadata": {"name": "home-server-backup-db-owner", "namespace": "home-server-backups"}})
        self.assertNotIn("data", sealed["spec"])
        job = docs["CronJob", "home-server-backup"]
        self.assertTrue(job["spec"]["suspend"])
        pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(docs["ServiceAccount", "home-server-backup"]["automountServiceAccountToken"])
        container = pod["containers"][0]
        env = {e["name"]: e for e in container["env"]}
        for name in ("PGUSER", "PGPASSWORD"):
            self.assertEqual(env[name]["valueFrom"]["secretKeyRef"]["name"], "home-server-backup-db-owner")
        self.assertEqual(env["PGSSLMODE"]["value"], "require")
        self.assertIn("limits", container["resources"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        volumes = {v["name"]: v for v in pod["volumes"]}
        self.assertEqual(volumes["identity"]["secret"]["secretName"], "home-server-backup-identity")
        self.assertEqual(volumes["identity"]["secret"]["defaultMode"], 0o400)
        config = docs["ConfigMap", "home-server-backup"]["data"]
        self.assertIn("credential_process = /usr/local/bin/aws_signing_helper", config["aws-config"])
        self.assertIn("trust-anchor/913f6b1b-d09a-41be-8715-e41e04ecda90", config["aws-config"])
        self.assertNotIn("aws_access_key_id", config["aws-config"])
        script = config["backup.sh"]
        self.assertEqual(script, (BACKUP / "scripts" / "backup.sh").read_text().rstrip("\n"))
        self.assertIn("postgres/$tier/cluster-$stamp", script)
        self.assertIn("age --encrypt -r", script)
        self.assertNotIn("recovery/", script.split("prefix=")[1])  # never writes the recovery prefix

    def test_enabling_requires_a_pinned_image_digest(self):
        result = subprocess.run(["helm", "template", "backup", str(BACKUP), "--set", "enabled=true", "--set", "image.digest="],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image.digest is required", result.stderr)
        half = subprocess.run(["helm", "template", "backup", str(BACKUP), "--set", "sealed.encryptedPassword="],
                              cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(half.returncode, 0)
        self.assertIn("must both be set", half.stderr)
        docs = render(BACKUP, namespace="home-server-backups",
                      sets=("sealed.encryptedUsername=", "sealed.encryptedPassword="))
        self.assertNotIn(("SealedSecret", "home-server-backup-db-owner"), docs)
        digest = "sha256:" + "ab" * 32
        docs = render(BACKUP, namespace="home-server-backups", sets=("enabled=true", f"image.digest={digest}"))
        job = docs["CronJob", "home-server-backup"]
        self.assertFalse(job["spec"]["suspend"])
        image = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["image"]
        self.assertEqual(image, f"ghcr.io/steve-droid/home-server-backup@{digest}")

    def test_children_are_gated_and_namespaced(self):
        backup = load(HOME_APPS / "backup.yaml")
        self.assertEqual(backup["spec"]["source"]["path"], "charts/home-server-backup")
        self.assertEqual(backup["spec"]["destination"]["namespace"], "home-server-backups")
        self.assertNotIn("CreateNamespace=true", backup["spec"].get("syncPolicy", {}).get("syncOptions", []))
        self.assertNotIn("helm", backup["spec"]["source"])  # values.yaml only: enabled=false is the gate
        heartbeat = load(HOME_APPS / "heartbeat.yaml")
        self.assertEqual(heartbeat["spec"]["source"]["path"], "charts/home-server-heartbeat")
        self.assertEqual(heartbeat["spec"]["destination"]["namespace"], "monitoring")
        self.assertNotIn("helm", heartbeat["spec"]["source"])


class HomeCloudflaredChartTests(unittest.TestCase):
    """charts/home-server-cloudflared: one replica with the sealed token; verified origin TLS only."""

    def test_committed_default_is_enabled_with_the_sealed_tunnel_token(self):
        docs = render(CLOUDFLARED, namespace="cloudflared")
        self.assertEqual(docs["Deployment", "home-server-cloudflared"]["spec"]["replicas"], 1)
        sealed = docs["SealedSecret", "home-server-cloudflared-token"]
        self.assertRegex(sealed["spec"]["encryptedData"]["token"], r"^Ag[A-Za-z0-9+/=]{500,}$")
        self.assertEqual(sealed["spec"]["template"]["metadata"]["namespace"], "cloudflared")
        self.assertIn(("ServiceMonitor", "home-server-cloudflared"), docs)

    def test_gated_render_is_scaled_to_zero_with_no_secret(self):
        docs = render(CLOUDFLARED, namespace="cloudflared", sets=("enabled=false", "sealed.encryptedToken="))
        self.assertEqual(set(docs), {("Deployment", "home-server-cloudflared"),
                                     ("ConfigMap", "home-server-cloudflared-ca"),
                                     ("Service", "home-server-cloudflared")})
        deploy = docs["Deployment", "home-server-cloudflared"]
        self.assertEqual(deploy["spec"]["replicas"], 0)
        pod = deploy["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(pod["hostNetwork"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        container = pod["containers"][0]
        self.assertRegex(container["image"], r"^cloudflare/cloudflared@sha256:[0-9a-f]{64}$")
        self.assertEqual(container["args"][:2], ["tunnel", "--no-autoupdate"])
        self.assertEqual(container["args"][-1], "run")
        self.assertFalse(any("tls-verify" in a or "url" in a for a in container["args"]),
                         "routes and origin TLS come from the remotely managed config; never noTLSVerify")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertIn("limits", container["resources"])
        env = {e["name"]: e for e in container["env"]}
        self.assertEqual(env["TUNNEL_TOKEN"]["valueFrom"]["secretKeyRef"],
                         {"name": "home-server-cloudflared-token", "key": "token"})
        self.assertNotIn("value", env["TUNNEL_TOKEN"])
        mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
        self.assertTrue(mounts["/etc/cloudflared/ca"]["readOnly"])
        ca = docs["ConfigMap", "home-server-cloudflared-ca"]["data"]["home-server-ca.crt"]
        self.assertTrue(ca.startswith("-----BEGIN CERTIFICATE-----"))
        self.assertNotIn("PRIVATE KEY", ca)
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/ready")

    def test_enabling_with_a_sealed_token_renders_secret_monitoring_and_one_replica(self):
        docs = render(CLOUDFLARED, namespace="cloudflared",
                      sets=("enabled=true", "sealed.encryptedToken=AgBciphertext"))
        self.assertEqual(docs["Deployment", "home-server-cloudflared"]["spec"]["replicas"], 1)
        sealed = docs["SealedSecret", "home-server-cloudflared-token"]
        self.assertEqual(sealed["spec"]["encryptedData"], {"token": "AgBciphertext"})
        self.assertEqual(sealed["spec"]["template"]["metadata"]["namespace"], "cloudflared")
        self.assertIn(("ServiceMonitor", "home-server-cloudflared"), docs)
        rules = docs["PrometheusRule", "home-server-cloudflared"]["spec"]["groups"][0]["rules"]
        by_name = {r["alert"]: r for r in rules}
        self.assertEqual(by_name["HomeServerTunnelDisconnected"]["labels"]["severity"], "critical")
        self.assertEqual(by_name["HomeServerTunnelDegraded"]["labels"]["severity"], "warning")

    def test_enabling_without_a_token_fails_to_render(self):
        result = subprocess.run(["helm", "template", "cloudflared", str(CLOUDFLARED),
                                 "--set", "enabled=true", "--set", "sealed.encryptedToken="],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sealed.encryptedToken", result.stderr)

    def test_child_targets_the_chart_in_its_own_namespace(self):
        child = load(HOME_APPS / "cloudflared.yaml")
        self.assertEqual(child["spec"]["source"]["path"], "charts/home-server-cloudflared")
        self.assertEqual(child["spec"]["destination"]["namespace"], "cloudflared")
        self.assertTrue(child["spec"]["syncPolicy"]["automated"]["prune"])


if __name__ == "__main__":
    unittest.main()
