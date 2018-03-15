"""The Olive XML OCR importer."""

import codecs
import copy
import json
import logging
import os
import re
import sys
import zipfile
from collections import deque
from operator import itemgetter

from bs4 import BeautifulSoup
from impresso_commons.path import canonical_path

logger = logging.getLogger(__name__)


def olive_toc_parser(toc_path, issue_dir, encoding="windows-1252"):
    """TODO."""
    with codecs.open(toc_path, 'r', encoding) as f:
        text = f.read()

    return {
        int(page.get('page_no')): {
            entity.get("id"): {
                "id": entity.get("id"),
                "canonical_id": canonical_path(
                    issue_dir,
                    name="i" + entity.get("index_in_doc").zfill(4),
                    extension=""
                ),
                "type": entity.get("entity_type"),
                "seq": int(entity.get("index_in_doc"))
            }
            for entity in page.find_all("entity")
        }
        for page in BeautifulSoup(text, 'lxml').find_all('page')
    }


def olive_parser(text):
    u"""Parse an Olive XML file (e.g. from Le Temps corpus).

    The main logic implemented here was derived from
    <https://github.com/dhlab-epfl/LeTemps-preprocessing/>. Each XML file
    corresponds to one article, as detected by Olive.

    :param text: a string with the textual xml file to parse
    :type text: string
    :rtype: a dictionary, with keys "meta", °stats", "legacy"
    """
    soup = BeautifulSoup(text, "lxml")
    root = soup.find("xmd-entity")
    page_no = root['page_no']
    identifier = root['id']
    language = root['language']
    title = soup.meta['name']
    publication = soup.meta['publication']
    entity_type = root['entity_type']
    issue_date = soup.meta['issue_date']

    out = {
        "meta": {
            "language": {},
            "type": {}
        },
        "regions": [],
        "stats": {},
        "legacy": {"continuation_from": None, "continuation_to": None},
    }
    out["meta"]["title"] = title
    out["meta"]["page_no"] = [int(page_no)]
    out["meta"]["language"]["raw"] = language
    out["meta"]["publication"] = publication
    out["meta"]["type"]["raw"] = entity_type
    out["meta"]["issue_date"] = issue_date

    new_region = {
        "coords": [],
        "paragraphs": []
    }

    new_paragraph = {
        "lines": []
    }

    new_line = {
        "coords": [],
        "tokens": []
    }

    new_token = {
        "coords": [],
        "text": ""
    }

    for primitive in soup.find_all("primitive"):

        # store coordinate of text areas (boxes) by page
        # 1) page number, 2) coordinate list
        region = copy.deepcopy(new_region)
        region["coords"] = [int(i) for i in primitive.get('box').split(" ")]
        region["page_no"] = page_no

        para = None
        line = None
        line_counter = 0

        for tag in primitive.find_all(recursive=False):

            if tag.name == "l":

                if para is None and line is None:
                    para = copy.deepcopy(new_paragraph)
                    line = copy.deepcopy(new_line)

                if line_counter > 0 and line is not None:
                    para["lines"].append(line)

                if tag.get("p") in ["S", "SA"] and line_counter > 0:
                    region["paragraphs"].append(para)
                    para = copy.deepcopy(new_paragraph)

                line = copy.deepcopy(new_line)
                line["coords"] = [
                    int(i)
                    for i in tag.get('box').split(" ")
                ]
                line_counter += 1

            if tag.name in ["w", "q"]:
                # TODO: store `style_ref` information

                # store coordinates of each token
                # 1) token, 2) page number, 3) coordinate list
                t = copy.deepcopy(new_token)
                t["coords"] = [int(i) for i in tag.get('box').split(" ")]
                t["text"] = tag.string
                t["style"] = int(tag.get('style_ref'))

                if tag.name == "q" and tag.get('qid') is not None:
                    qid = tag.get('qid')
                    normalized_form = soup.find('qw', qid=qid).text
                    t["norm_form"] = normalized_form

                    if len(line["tokens"]) > 1:
                        if "norm_form" not in line["tokens"][-1]:
                            t["skip"] = False
                        else:
                            t["skip"] = True

                # append the token to the line
                line["tokens"].append(t)

        # append orphan lines
        if line is not None:
            para["lines"].append(line)

        region["paragraphs"].append(para)

        if para is not None:
            out["regions"].append(region)

    out["legacy"]["id"] = identifier
    out["legacy"]["source"] = soup.link['source']
    out["legacy"]["word_count"] = int(soup.meta['wordcnt'])
    out["legacy"]["chars_count"] = int(soup.meta['total_chars_count'])
    suspicious_chars_count = int(soup.meta['suspicious_chars_count'])
    out["legacy"]["suspicious_chars_count"] = int(suspicious_chars_count)
    out["legacy"]["first_id"] = soup.link['first_id']
    out["legacy"]["last_id"] = soup.link['last_id']
    out["legacy"]["next_id"] = soup.link['next_id']
    out["legacy"]["prev_id"] = soup.link['prev_id']

    if root.has_attr('continuation_from'):
        out["legacy"]["continuation_from"] = root['continuation_from']

    if root.has_attr('continuation_to'):
        out["legacy"]["continuation_to"] = root['continuation_to']

    return out


def combine_article_parts(article_parts):
    """TODO.

    :param article_parts: one or more article parts
    :type article_parts: list of dict
    :rtype: a dictionary, with keys "meta", "fulltext", "stats", "legacy"
    """
    if len(article_parts) > 1:
        # if an article has >1 part, retain the metadata
        # from the first item in the list
        article_dict = {
            "meta": {},
            "fulltext": "",
            "stats": {},
            "legacy": {}
        }
        article_dict["legacy"]["id"] = [
            "{}.xml".format(ar["legacy"]["id"])
            for ar in article_parts
        ]
        article_dict["meta"]["type"] = {}
        article_dict["meta"]["type"]["raw"] =\
            article_parts[0]["meta"]["type"]["raw"]

        article_dict["meta"]["title"] = article_parts[0]["meta"]["title"]
        article_dict["meta"]["page_no"] = [
            int(n)
            for ar in article_parts
            for n in ar["meta"]["page_no"]
        ]

        article_dict["meta"]["language"] = {}
        article_dict["meta"]["language"]["raw"] =\
            article_parts[0]["meta"]["language"]["raw"]
        article_dict["meta"]["publication"] =\
            article_parts[0]["meta"]["publication"]
        article_dict["meta"]["issue_date"] =\
            article_parts[0]["meta"]["issue_date"]
    else:
        article_dict = next(iter(article_parts))
    return article_dict


def parse_styles(text):
    """Turn Olive style file into a dictionary.

    :param text: textual content of file `styleGallery.txt`
    :type text: str
    :rtype: list of dicts
    """
    styles = []
    regex = r'(\d{3})=("\D+?),(\d+\.?\d+),(\(.*?\))'

    for line in text.split("\r\n"):

        if line == "":
            continue

        n, font, font_size, color = re.match(regex, line).groups()
        styles.append(
            {
                "id": int(n),
                "font": font.replace('"', ""),
                "font_size": float(font_size),
                "rgb_color": [
                    int(i)
                    for i in color.replace("(", "").replace(")", "").split(",")
                ]
            }
        )

    return styles


def recompose_page(page_number, info_from_toc, page_elements):
    """Create a page document starting from a list of page documents.

    :param page_number: page number
    :type page_number: int
    :param info_from_toc: a dictionary with page element IDs (articles, ads.)
        as keys, and dictionaries as values
    :type info_from_toc:
    :param page_elements: articles or advertisements
    :type page_elements: list of dict

    It's here that `n` attributes are assigned to each region/para/line/token.
    """
    page = {
        "regions": []
    }
    ordered_elements = sorted(
        list(info_from_toc.values()), key=itemgetter('seq')
    )

    # put together the regions while keeping the order in the page
    for el in ordered_elements:

        # filter out the ids keeping only Ads or Articles
        # but escluing various pther files in the archive
        if ("Ar" not in el["id"] or "Ar" not in el["id"]):
            continue

        element = page_elements[el["id"]]

        for i, region in enumerate(element["regions"]):
            region["seq"] = i + 1
            region["partOf"] = el["canonical_id"]

        page["regions"] += element["regions"]

    paragraphs_count = 0
    token_count = 0
    line_count = 0

    for region in page["regions"]:

        for para in region["paragraphs"]:
            paragraphs_count += 1
            para["seq"] = paragraphs_count

            for line in para["lines"]:
                line_count += 1
                line["seq"] = line_count

                for token in line["tokens"]:
                    token_count += 1
                    token["seq"] = token_count

    return page


def olive_import_issue(issue_dir, out_dir, temp_dir=None):
    """Import newspaper issues from a directory structure.

    This function imports a set of newspaper issues from a directory
    structure containing a `Document.zip` file for each issue (in the case of
    Le Temps one issue per day).

    Each ZIP archive is read, and the XML files it contains are transformed
    into the Impresso canonical format, described by a JSON schema. Each
    resulting JSON article is validate against this schema and written to
    file, following an agreed-upon directory structure.

    The intermediate Olive XML files can be kept on request (useful for
    debugging) by passing a `temp_dir` parameter.

    :param issue_dir: an object representing a newspaper issue
    :type issue_dir: `IssueDir`

    :param out_dir: the output directory, where the resulting JSON files
        will be written.
    :type out_dir: string

    :param temp_dir: a directory where to write the intermediate Olive XML
        files (default is `None`); if  `None` these files are not kept.
    :type temp_dir: string

    :rtype: a tuple where [0] the `issue_dir`; [1] a boolean indicating whether
        the import was successful; [2] the exception message if [1] is `False`,
        otherwise `None`.
    """
    out_dir = os.path.join(out_dir, canonical_path(issue_dir, path_type="dir"))
    logger.info("Started importing '{}'...'".format(issue_dir.path))

    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    if temp_dir is not None:
        temp_dir = os.path.join(
            temp_dir,
            canonical_path(issue_dir, path_type="dir")
        )

    if temp_dir is not None and not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    # ns = schemas_to_classes()  # TODO: implement properly

    issue_contents = []

    working_archive = os.path.join(issue_dir.path, "Document.zip")
    if os.path.isfile(working_archive):
        try:
            archive = zipfile.ZipFile(working_archive)
        except Exception as e:
            print("Bad zip file.")
            print("Unexpected error:", sys.exc_info()[0])
            print(out_dir + ";bad zip file;\n")
            return (issue_dir, False, e)

        # parse the XML files in the .zip archive
        counter = 0
        article = []
        items = sorted(
            [
                item
                for item in archive.namelist()
                if ".xml" in item
                and not item.startswith("._")
                and ("/Ar" in item or "/Ad" in item)
            ]
        )

        logger.debug("Contents: {}".format(archive.namelist()))

        # if a `temp_dir` is passed as a parameter, do store the intermediate
        # Olive .xml files, otherwise just read without storing them
        if temp_dir is not None:
            for item in items:
                with codecs.open(
                    os.path.join(temp_dir, item.replace('/', '-')),
                    'wb'
                ) as out_file:
                    xml_data = archive.read(item)
                    out_file.write(xml_data)

        # TODO: parse the `styleGallery.txt` file
        if 'styleGallery.txt' in archive.namelist():
            styles = parse_styles(archive.read('styleGallery.txt').decode())

        # parse the TOC
        toc_path = os.path.join(issue_dir.path, "TOC.xml")
        toc_data = olive_toc_parser(toc_path, issue_dir)
        logger.debug(toc_data)

        logger.debug("XML files contained in {}: {}".format(
            working_archive,
            items
        ))
        articles = []
        while len(items) > 0:
            counter += 1

            # if out file already exists skip the data it contains
            # TODO: change this to work with the JSON output
            """
            if os.path.exists(out_file):
                exclude_data = BeautifulSoup(open(out_file).read())
                exclude_data = [
                    x.meta.id.string
                    for x in exclude_data.find_all("entity")
                ]
                for y in exclude_data:
                    for z in items:
                        if y in z:
                            items.remove(z)
                continue
            """

            internal_deque = deque([items[0]])
            items = items[1:]

            while len(internal_deque) > 0:
                item = internal_deque.popleft()
                # legacy code had: `archive.read(item, 'r')` which won't work
                xml_data = archive.read(item)
                new_data = olive_parser(xml_data)

                # check if it needs to be parsed later on
                if new_data["legacy"]['continuation_from'] is not None:
                    target = new_data["legacy"]["continuation_from"]
                    target = [x for x in items if target in x]
                    if len(target) > 0:
                        items.append(item)
                        continue

                article.append(new_data)

                if new_data["legacy"]['continuation_to'] is not None:
                    next_id = new_data["legacy"]["continuation_to"]
                    next_id = [x for x in items if next_id in x][0]
                    internal_deque.append(next_id)
                    items.remove(next_id)

            articles += article

            article = []

        # at this point the articles have been recomposed
        # but we still need to recompose pages
        for page_no in toc_data:

            info_from_toc = toc_data[page_no]
            element_ids = toc_data[page_no].keys()
            elements = {
                el["legacy"]["id"]: el
                for el in articles
                if (el["legacy"]["id"] in element_ids)
            }
            page = recompose_page(page_no, info_from_toc, elements)
            issue_contents += list(elements.values())

            # write the json page to file
            out_file = os.path.join(
                out_dir,
                canonical_path(issue_dir, "p" + str(page_no).zfill(4), ".json")
            )
            with codecs.open(out_file, 'w', 'utf-8') as f:
                json.dump(page, f)
                logger.info(
                    "Written page \'{}\' to {}".format(
                        page_no,
                        out_file
                    )
                )

        # TODO: combine info in `articles`
        content_items = [
            toc_data[pn][elid]
            for pn in toc_data.keys() for elid in toc_data[pn].keys()
        ]

        contents = {
            "articles": [i for i in content_items if i["type"] == "Article"],
            "ads": [i for i in content_items if i["type"] == "Ad"],
            "styles": styles
        }

        contents_filename = os.path.join(
            out_dir,
            canonical_path(issue_dir, "info", extension=".json")
        )

        with codecs.open(contents_filename, 'w', 'utf-8') as f:
            json.dump(contents, f)

    logger.debug("Done importing '{}'".format(out_dir))
    return (issue_dir, True, None)
