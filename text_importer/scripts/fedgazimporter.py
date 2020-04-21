from text_importer.importers import generic_importer
from text_importer.importers.fedgaz.classes import FedgazNewspaperIssue
from text_importer.importers.tetml.detect import (
    tetml_detect_issues,
    tetml_select_issues,
)

if __name__ == "__main__":
    generic_importer.main(FedgazNewspaperIssue, tetml_detect_issues, tetml_select_issues)
