"""
Functions and CLI script to convert Olive OCR data into Impresso's format.

Usage:
    impresso-txt-importer --input-dir=<id> --image-dir=<imgd> (--clear | --incremental) [--output-dir==<od> --s3-bucket=<b> --config-file=<cf> --log-file=<f> --temp-dir==<td> --verbose --parallelize]
    impresso-txt-importer --version

Options:
    --input-dir=<id>    Base directory containing one sub-directory for each journal
    --image-dir=<imgd>  Directory containing (canonical) images and their metadata
    --output-dir=<od>   Base directory where to write the output files
    --config-file=<cf>  configuration file for selective import
    --s3-bucket=<b>     If provided, writes output to an S3 drive, in the specified bucket
    --log-file=<f>      Log file; when missing print log to stdout
    --verbose           Verbose log messages (good for debugging)
    --clear             Removes the output folder (if already existing)
    --parallelize       Parallelize the import
    --filter=<ft>       Criteria to filter issues before import ("journal=GDL; date=1900/01/01-1950/12/31;")
    --version
"""  # noqa: E501

import json
import logging
import os
import shutil
from datetime import date

import ipdb as pdb  # remove from production version
from docopt import docopt

import dask
from dask import compute, delayed
from dask.diagnostics import ProgressBar
from dask.multiprocessing import get as mp_get
from impresso_commons.path import detect_issues, select_issues
from impresso_commons.path import detect_canonical_issues
from impresso_commons.path import KNOWN_JOURNALS
from text_importer import __version__
from text_importer.importers.olive import olive_import_issue

__author__ = "Matteo Romanello"
__email__ = "matteo.romanello@epfl.ch"
__organisation__ = "impresso @ DH Lab, EPFL"
__copyright__ = "EPFL, 2017"
__status__ = "development"

logger = logging.getLogger()

punctuation_nows_before = [".", ",", ")", "]", "}", "°", "..."]
punctuation_nows_after = ["(", "[", "{"]
punctuation_nows_beforeafter = ["'", "-"]
punctuation_ciffre = [".", ","]

html_escape_table = {
    "&amp;": "&",
    "&quot;": '"',
    "&apos;": "'",
    "&gt;": ">",
    "&lt;": "<",
}


def _parse_filter(filter_string):
    filters = {
        f.split("=")[0].strip(): f.split("=")[1].strip().split(",")
        for f in filter_string.split(";")
    }

    return filters


def _apply_filters(filter_dict, issues):

    filtered_issues = []

    if "journal" in filter_dict:
        filtered_issues = [
            i for i in issues if i.journal == filter_dict["journal"]
        ]
    else:
        filtered_issues = issues

    if "date" in filter_dict:

        # date filter is a range
        if "-" in filter_dict["date"]:
            start, end = filter_dict["date"].split("-")
            start = date(*[int(x) for x in start.split("/")])
            end = date(*[int(x) for x in end.split("/")])
            print(start, end)
            filtered_issues = [
                i
                for i in filtered_issues
                if i.date >= start and i.date <= end
            ]

        # date filter is not a range
        else:
            filter_date = date(*[
                int(x) for x in filter_dict["date"].split("/")
            ])

            filtered_issues += [
                i
                for i in issues
                if i.date == filter_date
            ]

    return filtered_issues


def main():
    """Execute the main with CLI parameters."""

    # store CLI parameters
    args = docopt(__doc__)
    inp_dir = args["--input-dir"]
    img_dir = args["--image-dir"]
    outp_dir = args["--output-dir"]
    out_bucket = args["--s3-bucket"]
    temp_dir = args["--temp-dir"]
    log_file = args["--log-file"]
    parallel_execution = args["--parallelize"]
    clear_output = args["--clear"]
    incremental_output = args["--incremental"]
    log_level = logging.DEBUG if args["--verbose"] else logging.INFO
    print_version = args["--version"]
    config_file = args["--config-file"]

    if print_version:
        print(f'impresso-txt-importer v{__version__}')
        return

    # Initialise the logger
    global logger
    logger.setLevel(log_level)

    if(log_file is not None):
        handler = logging.FileHandler(filename=log_file, mode='w')
    else:
        handler = logging.StreamHandler()

    formatter = logging.Formatter(
        '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.info("Logger successfully initialised")

    logger.debug("CLI arguments received: {}".format(args))

    # clean output directory if existing
    if outp_dir is not None and os.path.exists(outp_dir):
        if clear_output is not None and clear_output:
            shutil.rmtree(outp_dir)

    # clean temp directory if existing
    if temp_dir is not None and os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)

    # detect/select issues
    if config_file and os.path.isfile(config_file):
        logger.info(f"Found config file: {os.path.realpath(config_file)}")
        with open(config_file, 'r') as f:
            config = json.load(f)
            issues = select_issues(config, inp_dir)

        logger.info(
            "{} newspaper remained after applying filter: {}".format(
                len(issues),
                issues
            )
        )
    else:
        logger.info("No config file found.")
        issues = detect_issues(inp_dir)
        print(f'{len(issues)} newspaper issues detected')

    if os.path.exists(outp_dir) and incremental_output:
        issues_to_skip = [
            (issue.journal, issue.date, issue.edition)
            for issue in detect_canonical_issues(outp_dir, KNOWN_JOURNALS)
        ]
        logger.debug(f"Issues to skip: {issues_to_skip}")
        logger.info(f"{len(issues_to_skip)} issues to skip")
        issues = list(
            filter(
                lambda x: (x.journal, x.date, x.edition) not in issues_to_skip,
                issues
            )
        )
        logger.debug(f"Remaining issues: {issues}")
        logger.info(f"{len(issues)} remaining issues")
    # pdb.set_trace()

    logger.debug("Following issues will be imported:{}".format(issues))

    assert outp_dir is not None or out_bucket is not None
    """
    if outp_dir is not None:
        result = [
            olive_import_issue(
                i,
                img_dir,
                out_dir=outp_dir,
                temp_dir=temp_dir
            )
            for i in issues
        ]
    elif out_bucket is not None:
        result = [
            olive_import_issue(
                i,
                img_dir,
                s3_bucket=out_bucket,
                temp_dir=temp_dir
            )
            for i in issues
        ]

    """
    # prepare the execution of the import function
    tasks = [
        delayed(olive_import_issue)(
            i,
            img_dir,
            out_dir=outp_dir,
            s3_bucket=out_bucket,
            temp_dir=temp_dir
        )
        for i in issues
    ]

    print(
        "\nImporting {} newspaper issues...(parallelized={})".format(
            len(issues),
            parallel_execution
        )
    )
    with ProgressBar():
        if parallel_execution:
            result = compute(*tasks, get=mp_get)
        else:
            result = compute(*tasks, get=dask.get)
    print("Done.\n")
    logger.debug(result)

    # write a sort of report to a TSV file
    report = "\n".join(
        [
            f'{issue.path}\t{success}\t{error}'
            for issue, success, error in result
        ]
    )
    with open(os.path.join(outp_dir, "result.tsv"), 'w') as report_file:
        report_file.write(report)


if __name__ == '__main__':
    main()
