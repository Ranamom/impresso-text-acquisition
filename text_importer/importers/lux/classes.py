import os
import codecs
from bs4.element import NavigableString, Tag
from bs4 import BeautifulSoup
from time import strftime
from text_importer.helpers import get_issue_schema
from text_importer.importers.lux.core import convert_coordinates, encode_ark
from text_importer.importers.lux import alto

IssueSchema = get_issue_schema()

IIIF_ENDPOINT_URL = "https://iiif.eluxemburgensia.lu/iiif/2"


class LuxNewspaperPage(object):
    """Class representing a page in BNL data."""

    def __init__(self, n, id, filename, basedir):
        self.number = n
        self.id = id
        self.filename = filename
        self.basedir = basedir
        self.issue = None
        self.data = {
            'id': id,
            'cdt': None,
            'r': []  # here go the page regions
        }

    def add_issue(self, issue):
        self.issue = issue
        encoded_ark_id = encode_ark(self.issue.ark_id)
        iiif_base_link = f'{IIIF_ENDPOINT_URL}/{encoded_ark_id}'
        iiif_link = f'{iiif_base_link}%2fpages%2f{self.number}/info.json'
        self.data['iiif'] = iiif_link
        return

    def to_json(self):
        pass

    def parse(self):

        doc = self.xml

        mappings = {}
        for ci in self.issue._issue_data['i']:
            ci_id = ci['m']['id']
            if 'parts' in ci['l']:
                for part in ci['l']['parts']:
                    mappings[part['comp_id']] = ci_id

        pselement = doc.find('PrintSpace')
        self.data["r"] = alto.parse_printspace(pselement, mappings)

        # TODO: convert the coordinates
        return

    @property
    def xml(self):
        """Returns a BeautifulSoup object with Alto XML of the page."""
        alto_xml_path = os.path.join(self.basedir, self.filename)

        with codecs.open(alto_xml_path, 'r', "utf-8") as f:
            raw_xml = f.read()

        alto_doc = BeautifulSoup(raw_xml, 'xml')
        return alto_doc


class LuxNewspaperIssue(object):
    """docstring for MetsNewspaperIssue."""

    def __init__(self, issue_dir):

        # create the canonical issue id
        self.id = "{}-{}-{}".format(
            issue_dir.journal,
            "{}-{}-{}".format(
                issue_dir.date.year,
                issue_dir.date.month,
                issue_dir.date.day
            ),
            issue_dir.edition
        )
        self.journal = issue_dir.journal
        self.path = issue_dir.path
        self.date = issue_dir.date
        self._issue_data = {}
        self.image_properties = {}
        self.ark_id = None

        self._find_pages()
        self._parse_mets()

    def _find_pages(self):
        """Detects the Alto XML page files for a newspaper issue."""

        # get the canonical names for pages in the newspaper issue by
        # visiting the `text` sub-folder with the alto XML files
        text_path = os.path.join(self.path, 'text')
        page_file_names = [
            file
            for file in os.listdir(text_path)
            if not file.startswith('.') and '.xml' in file
        ]
        page_numbers = [
            int(fname.split('-')[-1].replace('.xml', ''))
            for fname in page_file_names
        ]
        page_canonical_names = [
            "{}-p{}".format(self.id, str(page_n).zfill(4))
            for page_n in page_numbers
        ]

        self.pages = []
        for filename, page_no, page_id in zip(
            page_file_names, page_numbers, page_canonical_names
        ):
            self.pages.append(
                LuxNewspaperPage(page_no, page_id, filename, text_path)
            )

    def _parse_mets_sections(self, mets_doc):
        # returns a list of content items
        # enforce some sorting
        content_items = []
        sections = mets_doc.findAll('dmdSec')

        # enforce sorting based on the ID string to pinpoint the
        # generated canonical IDs
        sections = sorted(
            sections,
            key=lambda elem: elem.get('ID').split("_")[1]
        )

        for item_counter, section in enumerate(sections):

            section_id = section.get('ID')

            if 'ARTICLE' in section_id:
                item_counter += 1
                lang = section.find_all('languageTerm')[0].getText()
                title_elements = section.find_all('titleInfo')
                item_title = title_elements[0].getText().replace('\n', ' ')\
                    .strip() if len(title_elements) > 0 else None
                metadata = {
                    'id': "{}-i{}".format(self.id, str(item_counter).zfill(4)),
                    't': item_title,
                    'l': lang,
                    'tp': 'ar',
                    'pp': []
                }
                item = {
                    "m": metadata,
                    "l": {
                        # TODO: pass the article components
                        "id": section_id
                    }
                }
                content_items.append(item)
            elif 'PICT' in section_id:
                metadata = {
                    'id': "{}-i{}".format(self.id, str(item_counter).zfill(4)),
                    't': None,
                    'l': 'n/a',
                    'tp': 'image',  # TODO: check!
                    'pp': []
                }
                item = {
                    "m": metadata,
                    "l": {
                        # TODO: pass the article components
                        "id": section_id
                    }
                }
                content_items.append(item)
        return content_items

    def _parse_mets_div(self, element):
        # to each section_id corresponds a div
        # find first-level DIVs inside the element
        # and inside each div get to the <area>
        # return a dict with component_id, component_role, component_fileid

        parts = []

        for child in element.children:

            comp_id = None
            comp_role = None
            comp_fileid = None

            if isinstance(child, NavigableString):
                continue
            elif isinstance(child, Tag):
                comp_role = child.get('TYPE').lower()
                areas = child.findAll('area')
                for area in areas:
                    comp_id = area.get('BEGIN')
                    comp_fileid = area.get('FILEID')
                    comp_page_no = int(comp_fileid.replace('ALTO', ''))

                    parts.append(
                        {
                            'comp_role': comp_role,
                            'comp_id': comp_id,
                            'comp_fileid': comp_fileid,
                            'comp_page_no': comp_page_no  # it's a bit quick and dirty
                        }
                    )
        return parts

    def _parse_mets_filegroup(self, element):
        # return a list of page image ids

        return {
            int(child.get("SEQ")): child.get("ADMID")
            for child in element.findAll('file')
        }

    def parse_mets_amdsec(self, mets_doc):
        image_filegroup = mets_doc.findAll('fileGrp', {'USE': 'Images'})[0]
        page_image_ids = self._parse_mets_filegroup(image_filegroup)
        amd_sections = {
            image_id:  mets_doc.findAll('amdSec', {'ID': image_id})[0]
            for image_id in page_image_ids.values()
        }
        image_properties_dict = {}
        for image_no, image_id in page_image_ids.items():
            amd_sect = amd_sections[image_id]
            image_properties_dict[image_no] = {
                'x_resolution': int(amd_sect.find('xOpticalResolution').text),
                'y_resolution': int(amd_sect.find('yOpticalResolution').text)
            }
        return image_properties_dict

    def _parse_mets(self):
        """Parses the Mets XML file of the newspaper issue."""

        mets_file = [
            os.path.join(self.path, f)
            for f in os.listdir(self.path)
            if 'mets.xml' in f
        ][0]

        with codecs.open(mets_file, 'r', "utf-8") as f:
            raw_xml = f.read()

        mets_doc = BeautifulSoup(raw_xml, 'xml')

        # explain
        self.image_properties = self.parse_mets_amdsec(mets_doc)

        content_items = self._parse_mets_sections(mets_doc)

        ark_link = mets_doc.find('mets').get('OBJID')
        self.ark_id = ark_link.replace('https://persist.lu/', '')

        for ci in content_items:
            legacy_id = ci['l']['id']
            item_div = mets_doc.findAll('div', {'DMDID': legacy_id})[0]
            ci['l']['parts'] = self._parse_mets_div(item_div)

            if ci['m']['tp'] == 'image':
                # import ipdb; ipdb.set_trace()
                # for each "part" open the XML file of corresponding page
                # get the coordinates and convert them
                # some imgs are in fact tables (meaning they have text
                # recognized) this we can know it only once we open the alto
                # file
                assert len(ci['l']['parts']) == 1
                part = ci['l']['parts'][0]
                curr_page = None

                for page in self.pages:
                    if page.number == part['comp_page_no']:
                        curr_page = page

                assert curr_page is not None
                if curr_page.number not in ci['m']['pp']:
                    ci['m']['pp'].append(curr_page.number)

                composed_block = curr_page.xml.find(
                    'ComposedBlock',
                    {"ID": part['comp_id']}
                )
                if composed_block.get('TYPE') == "Table":
                    ci['m']['tp'] = 'table'
                    pass
                elif composed_block.get('TYPE') == "Illustration":
                    graphic_el = composed_block.find('GraphicalElement')
                    hpos = int(graphic_el.get('HPOS'))
                    vpos = int(graphic_el.get('VPOS'))
                    width = int(graphic_el.get('WIDTH'))
                    height = int(graphic_el.get('HEIGHT'))
                    img_props = self.image_properties[curr_page.number]
                    x_resolution = img_props['x_resolution']
                    y_resolution = img_props['y_resolution']
                    coordinates = convert_coordinates(
                        hpos,
                        vpos,
                        height,
                        width,
                        x_resolution,
                        y_resolution
                    )
                    encoded_ark_id = encode_ark(self.ark_id)
                    iiif_base_link = f'{IIIF_ENDPOINT_URL}/{encoded_ark_id}'
                    ci['m']['iiif_link'] = f'{iiif_base_link}%2fpages%2f{curr_page.number}/info.json'
                    ci['c'] = list(coordinates)
                    del ci['l']['parts']

            elif ci['m']['tp'] == 'ar':
                for part in ci['l']['parts']:
                    page_no = part["comp_page_no"]
                    if page_no not in ci['m']['pp']:
                        ci['m']['pp'].append(page_no)

        self._issue_data = {
            "cdt": strftime("%Y-%m-%d %H:%M:%S"),
            "i": content_items,
            "id": self.id,
            "pp": [p.id for p in self.pages]
        }

    @property
    def xml(self):
        mets_file = [
            os.path.join(self.path, f)
            for f in os.listdir(self.path)
            if 'mets.xml' in f
        ][0]

        with codecs.open(mets_file, 'r', "utf-8") as f:
            raw_xml = f.read()

        mets_doc = BeautifulSoup(raw_xml, 'xml')
        return mets_doc

    def to_json(self):
        issue = IssueSchema(**self._issue_data)
        return issue.serialize()
