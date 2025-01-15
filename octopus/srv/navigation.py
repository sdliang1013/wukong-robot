from octopus.robot import constants, utils
from octopus.schemas.core import TxtEntity, Paginate, Page

class FaqService:

    def __init__(self, file: str):
        self.faq_list = TxtEntity(file=constants.getConfigData(file))

    def faq_all(self) -> list:
        return self.faq_list.serialize()

    def query_faq_page(self, paginate: Paginate, kw: str = None) -> Page:
        content = self.faq_all()
        paginate.set_total(len(content))
        return Page(
            paginate=paginate,
            content=utils.page_list(
                data=content, page=paginate.page, paginate_by=paginate.paginate_by
            ),
        )
