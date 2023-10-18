import bz2
import json
import logging
import os
from glob import glob

from contextlib import ExitStack

from text_importer.utils import get_pkg_resource
from text_importer.importers import CONTENTITEM_TYPE_IMAGE
from text_importer.importers.core import import_issues
from text_importer.importers.rero.classes import ReroNewspaperIssue
from text_importer.importers.rero.detect import detect_issues

logger = logging.getLogger(__name__)


def test_import_issues():
    """Test the RERO XML importer with sample data."""
    
    logger.info("Starting test_import_issues in test_rero_importer.py.")
    
    f_mng = ExitStack()
    inp_dir = get_pkg_resource(f_mng, 'data/sample_data/RERO2/')
    ar_file = get_pkg_resource(f_mng, 
                           'data/sample_data/RERO2/rero2_access_rights.json')
    out_dir = get_pkg_resource(f_mng, 'data/out/')

    issues = detect_issues(base_dir=inp_dir, access_rights=ar_file)

    assert issues is not None
    assert len(issues) > 0
    
    import_issues(
        issues,
        out_dir=out_dir,
        s3_bucket=None,
        issue_class=ReroNewspaperIssue,
        temp_dir=None,
        image_dirs=None,
        chunk_size=None
    )
    
    logger.info("Finished test_import_issues, closing file manager.")
    f_mng.close()


def check_image_coordinates(issue_data):
    items = issue_data['i']
    imgs = [i for i in items if i['m']['tp'] == CONTENTITEM_TYPE_IMAGE]
    return (len(imgs) == 0 or 
            all('c' in data['m'] and len(data['m']['c']) == 4 for data in imgs))


def test_image_coordinates():

    logger.info("Starting test_image_coordinates in test_rero_importer.py")
    f_mng = ExitStack()
    inp_dir = get_pkg_resource(f_mng, 'data/sample_data/RERO2/')
    ar_file = get_pkg_resource(f_mng, 
                           'data/sample_data/RERO2/rero2_access_rights.json')
    out_dir = get_pkg_resource(f_mng, 'data/out/')
    
    issues = detect_issues(
        base_dir=inp_dir,
        access_rights=ar_file
    )
    
    assert issues is not None
    assert len(issues) > 0
    
    journals = set([x.journal for x in issues])
    blobs = [f"{j}*.jsonl.bz2" for j in journals]
    issue_files = [f for b in blobs for f in glob(os.path.join(out_dir, b))]
    logger.info(issue_files)
    
    for filename in issue_files:
        with bz2.open(filename, "rt") as bzinput:
            for line in bzinput:
                issue = json.loads(line)
                assert check_image_coordinates(issue), (
                    "Images do not have coordinates"
                )

    logger.info("Finished test_image_coordinate, closing file manager.")
    f_mng.close()
