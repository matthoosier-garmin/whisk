#! /usr/bin/env python3
#
# 2020 Garmin Ltd. or its subsidiaries
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import itertools
import json
import jsonschema
import os
import pathlib
import string
import sys
import tabulate
import textwrap
import yaml

tabulate.PRESERVE_WHITESPACE = True

THIS_DIR = pathlib.Path(__file__).parent.absolute()

CACHE_VERSION = 1


class ConfTemplate(string.Template):
    delimiter = r"%"


def print_items(items, is_current, extra=[]):
    def get_current(i):
        if is_current(i):
            return " *"
        return "  "

    print(
        tabulate.tabulate(
            [
                (
                    get_current(i),
                    i,
                    items[i].get("description", ""),
                )
                for i in sorted(items)
            ]
            + [(get_current(e), e, "") for e in extra],
            tablefmt="plain",
        )
    )


def print_modes(conf, cur_mode):
    print_items(conf["modes"], lambda m: m == cur_mode)


def print_sites(conf, cur_site):
    print_items(conf["sites"], lambda s: s == cur_site)


def print_products(conf, cur_products):
    print_items(conf["products"], lambda p: p in cur_products)


def print_versions(conf, cur_version):
    print_items(conf["versions"], lambda v: v == cur_version, extra=["default"])


def write_hook(f, conf, hook):
    f.write(conf.get("hooks", {}).get(hook, ""))
    f.write("\n")


def configure(sys_args):
    parser = argparse.ArgumentParser(description="Configure build")
    parser.add_argument(
        "--products", action="append", default=[], help="Change build product(s)"
    )
    parser.add_argument("--mode", help="Change build mode")
    parser.add_argument("--site", help="Change build site")
    parser.add_argument("--version", help="Set Yocto version")
    parser.add_argument("--build-dir", help="Set build directory")
    parser.add_argument("--list", action="store_true", help="List options")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write out new config files (useful if product configuration has changed)",
    )
    parser.add_argument(
        "--no-config",
        "-n",
        action="store_true",
        help="Ignore cached user configuration",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress non-error output"
    )

    user_args = parser.parse_args(sys_args.user_args)

    with sys_args.conf.open("r") as f:
        env = os.environ.copy()
        env["WHISK_PROJECT_ROOT"] = sys_args.root.absolute()
        conf = yaml.load(
            ConfTemplate(f.read()).substitute(**env),
            Loader=yaml.Loader,
        )

    if not "version" in conf:
        print("Config file '%s' missing version" % sys_args.conf)
        return 1

    if conf["version"] != 1:
        print("Bad version %r in config file '%s'" % (conf["version"], sys_args.conf))
        return 1

    try:
        with (THIS_DIR / "whisk.schema.json").open("r") as f:
            jsonschema.validate(conf, json.load(f))
    except jsonschema.ValidationError as e:
        print("Error validating %s: %s" % (sys_args.conf, e.message))
        return 1

    def get_product(name):
        nonlocal conf
        if name == "core":
            return conf.get("core", {})
        return conf["products"][name]

    cache_path = pathlib.Path(conf.get("cache", sys_args.root / ".config.yaml"))
    cache = {}
    if not user_args.no_config:
        try:
            with cache_path.open("r") as f:
                cache = yaml.load(f, Loader=yaml.Loader)
        except OSError:
            pass

        try:
            if cache.get("cache_version") != CACHE_VERSION:
                cache = {}
        except AttributeError:
            cache = {}

    defaults = conf.get("defaults", {})

    cur_mode = cache.get("mode", defaults.get("mode"))
    cur_products = cache.get("products", defaults.get("products", []))
    cur_site = cache.get("site", defaults.get("site"))
    cur_version = cache.get("version", "default")
    cur_actual_version = cache.get("actual_version", "")
    build_dir = pathlib.Path(cache.get("build_dir", defaults.get("build_dir", "build")))

    write = user_args.write or sys_args.init

    if user_args.list:
        print("Possible products:")
        print_products(conf, cur_products)
        print("Possible modes:")
        print_modes(conf, cur_mode)
        print("Possible sites:")
        print_sites(conf, cur_site)
        print("Possible versions:")
        print_versions(conf, cur_version)
        return 0

    if user_args.products:
        write = True
        user_products = sorted(
            set(itertools.chain(*(a.split() for a in user_args.products)))
        )
        for p in user_products:
            if not p in conf.get("products", {}):
                print("Unknown product '%s'. Please choose from:" % p)
                print_products(conf, cur_products)
                return 1
        cur_products = user_products

    if user_args.mode:
        write = True
        if user_args.mode not in conf["modes"]:
            print("Unknown mode '%s'. Please choose from:" % user_args.mode)
            print_modes(conf, cur_mode)
            return 1
        cur_mode = user_args.mode

    if user_args.site:
        write = True
        if user_args.site not in conf["sites"]:
            print("Unknown site '%s'. Please choose from:" % user_args.site)
            print_sites(conf, cur_site)
            return 1
        cur_site = user_args.site

    if user_args.version:
        write = True
        if sys_args.init:
            if (
                user_args.version != "default"
                and user_args.version not in conf["versions"]
            ):
                print("Unknown version '%s'. Please choose from:" % user_args.version)
                print_versions(conf, cur_version)
                return 1

            cur_version = user_args.version
        elif user_args.version != cur_version:
            print(
                "The version cannot be changed after the environment is initialized. Please initialize a new environment with '--version=%s'"
                % user_args.version
            )
            return 1

    if user_args.build_dir:
        if not sys_args.init:
            print(
                "Build directory cannot be changed after environment is initialized. Please initialize a new environment with '--build-dir=%s'"
                % user_args.build_dir
            )
            return 1
        build_dir = pathlib.Path(user_args.build_dir)

    if not cur_products:
        print("One or more products must be specified with --product")
        return 1

    if not cur_mode:
        print("A build mode must be specified with --mode")
        return 1

    if not cur_site:
        print("A site must be specified with --site")
        return 1

    # Set the actual version
    if sys_args.init:
        cur_actual_version = None

    if cur_version == "default":
        for p in cur_products:
            v = conf["products"][p]["default_version"]
            if cur_actual_version is None:
                cur_actual_version = v
            elif v != cur_actual_version:
                print(
                    "{product} is incompatible with other products: {v} != {actual}".format(
                        product=p, v=v, actual=cur_actual_version
                    )
                )
                return 1
    else:
        cur_actual_version = cur_version

    version = conf["versions"][cur_actual_version]

    cur_layers = {l["name"]: l["paths"] for l in version.get("layers", [])}

    # Sanity check that all configured products have layers
    for p in ["core"] + cur_products:
        missing = set(
            l for l in get_product(p).get("layers", []) if not l in cur_layers
        )
        if missing:
            print(
                "Product '{product}' requires layer collection(s) '{layers}' which is not present in version '{version}'".format(
                    product=p, layers=" ".join(missing), version=cur_actual_version
                )
            )
            return 1

    with sys_args.env.open("w") as f:
        f.write(
            textwrap.dedent(
                """\
                export WHISK_PRODUCTS="{products}"
                export WHISK_MODE="{mode}"
                export WHISK_SITE="{site}"
                export WHISK_VERSION="{version}"
                export WHISK_ACTUAL_VERSION="{actual_version}"

                export WHISK_BUILD_DIR={build_dir}
                export WHISK_INIT={init}
                """
            ).format(
                products=" ".join(cur_products),
                mode=cur_mode,
                site=cur_site,
                version=cur_version,
                actual_version=cur_actual_version,
                build_dir=str(build_dir.absolute()),
                init="true" if sys_args.init else "false",
            )
        )

        write_hook(f, conf, "pre_init")
        if sys_args.init:
            bitbake_dir = version.get("bitbakedir")
            if bitbake_dir:
                f.write('export BITBAKEDIR="%s"\n' % bitbake_dir)

            f.write(
                'export BB_ENV_EXTRAWHITE="${BB_ENV_EXTRAWHITE} WHISK_PROJECT_ROOT WHISK_PRODUCTS WHISK_MODE WHISK_SITE WHISK_ACTUAL_VERSION"\n'
            )

            if version.get("pyrex"):
                f.write(
                    textwrap.dedent(
                        """\
                        PYREX_CONFIG_BIND="{root}"
                        PYREX_ROOT="{version[pyrex][root]}"
                        PYREX_OEINIT="{version[oeinit]}"
                        PYREXCONFFILE="{version[pyrex][conf]}"

                        . {version[pyrex][root]}/pyrex-init-build-env $WHISK_BUILD_DIR
                        """
                    ).format(
                        root=sys_args.root.absolute(),
                        version=version,
                    )
                )

            else:
                f.write(
                    ". {version[oeinit]} $WHISK_BUILD_DIR\n".format(version=version)
                )

        write_hook(f, conf, "post_init")

        f.write("unset WHISK_BUILD_DIR WHISK_INIT\n")

    if not user_args.no_config:
        with cache_path.open("w") as f:
            f.write(
                yaml.dump(
                    {
                        "cache_version": CACHE_VERSION,
                        "mode": cur_mode,
                        "products": cur_products,
                        "site": cur_site,
                        "version": cur_version,
                        "actual_version": cur_actual_version,
                        "build_dir": str(build_dir.absolute()),
                    },
                    Dumper=yaml.Dumper,
                )
            )

    if write:
        (build_dir / "conf" / "multiconfig").mkdir(parents=True, exist_ok=True)

        with (build_dir / "conf" / "site.conf").open("w") as f:
            f.write("# This file was dynamically generated by whisk\n")

            f.write(conf["sites"][cur_site].get("conf", ""))
            f.write("\n")
            f.write(conf["modes"][cur_mode].get("conf", ""))
            f.write("\n")

            f.write(
                textwrap.dedent(
                    """\

                    WHISK_PRODUCT ?= "core"

                    # Set TMPDIR to a version specific location
                    TMPDIR_BASE ?= "${TOPDIR}/tmp/${WHISK_MODE}/${WHISK_ACTUAL_VERSION}"
                    DEPLOY_DIR_BASE ?= "${TOPDIR}/deploy/${WHISK_MODE}/${WHISK_ACTUAL_VERSION}"

                    TMPDIR = "${TMPDIR_BASE}/${WHISK_PRODUCT}"

                    # Set the deploy directory to output to a well-known location
                    DEPLOY_DIR = "${DEPLOY_DIR_${WHISK_PRODUCT}}"
                    DEPLOY_DIR_IMAGE = "${DEPLOY_DIR}/images"

                    DEPLOY_DIR_core = "${DEPLOY_DIR_BASE}/core"
                    """
                )
            )
            f.write(
                'WHISK_TARGETS_core = "%s"\n'
                % (" ".join("${WHISK_TARGETS_%s}" % p for p in cur_products))
            )

            for p in sorted(conf["products"]):
                f.write(
                    textwrap.dedent(
                        """\
                        DEPLOY_DIR_{p} = "${{DEPLOY_DIR_BASE}}/{p}"
                        WHISK_TARGETS_{p} = "{targets}"
                        """
                    ).format(
                        p=p,
                        targets=" ".join(
                            sorted(conf["products"][p].get("targets", []))
                        ),
                    )
                )

            f.write("\n")

            multiconfigs = set("product-%s" % p for p in cur_products)
            for p in cur_products:
                multiconfigs |= set(conf["products"][p].get("multiconfigs", []))

            f.write(
                textwrap.dedent(
                    """\
                    BBMULTICONFIG = "{multiconfigs}"
                    BBMASK += "${{BBMASK_${{WHISK_PRODUCT}}}}"

                    BB_HASHBASE_WHITELIST_append = " WHISK_PROJECT_ROOT"
                    """
                ).format(multiconfigs=" ".join(sorted(multiconfigs)))
            )

            f.write(conf.get("core", {}).get("conf", ""))
            f.write("\n")

        for name, p in conf["products"].items():
            with (build_dir / "conf" / "multiconfig" / ("product-%s.conf" % name)).open(
                "w"
            ) as f:
                f.write(
                    textwrap.dedent(
                        """\
                        # This file was dynamically generated by whisk
                        WHISK_PRODUCT = "{product}"
                        WHISK_PRODUCT_DESCRIPTION = "{description}"

                        """
                    ).format(
                        product=name,
                        description=p.get("description", ""),
                    )
                )

                f.write(p.get("conf", ""))
                f.write("\n")

        with (build_dir / "conf" / "bblayers.conf").open("w") as f:
            f.write(
                textwrap.dedent(
                    """\
                    # This file was dynamically generated by whisk
                    BBPATH = "${TOPDIR}"
                    BBFILES ?= ""

                    """
                )
            )

            requested_layers = set()

            for name in ["core"] + cur_products:
                product_layers = get_product(name).get("layers", [])
                requested_layers.update(product_layers)

                for l, paths in cur_layers.items():
                    if not l in product_layers:
                        for p in paths:
                            f.write('BBMASK_%s += "%s"\n' % (name, p))
                f.write("\n")

            for l in version.get("layers", []):
                if l["name"] in requested_layers:
                    for p in l["paths"]:
                        f.write('BBLAYERS += "%s"\n' % p)

            f.write('BBLAYERS += "%s/meta-whisk"\n\n' % THIS_DIR)

            f.write("\n")

            f.write(
                textwrap.dedent(
                    """\
                    # This line gives devtool a place to add its layers
                    BBLAYERS += ""
                    """
                )
            )

    if write and not sys_args.init:
        return 0

    if not user_args.quiet:
        print("PRODUCT    = %s" % " ".join(cur_products))
        print("MODE       = %s" % cur_mode)
        print("SITE       = %s" % cur_site)
        print("VERSION    = %s" % cur_version, end="")
        if cur_version != cur_actual_version:
            print(" (%s)" % cur_actual_version)
        else:
            print()

    return 0


def main():
    parser = argparse.ArgumentParser(description="Whisk product manager")

    subparser = parser.add_subparsers(dest="command")
    subparser.required = True

    configure_parser = subparser.add_parser(
        "configure", help="Configure build environment"
    )

    configure_parser.add_argument("--root", help="Project root", type=pathlib.Path)
    configure_parser.add_argument(
        "--conf", help="Project configuration file", type=pathlib.Path
    )
    configure_parser.add_argument(
        "--init", action="store_true", help="Run first-time initialization"
    )
    configure_parser.add_argument(
        "--env", help="Path to environment output file", type=pathlib.Path
    )
    configure_parser.add_argument("user_args", nargs="*", help="User arguments")
    configure_parser.set_defaults(func=configure)

    args = parser.parse_args()

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
