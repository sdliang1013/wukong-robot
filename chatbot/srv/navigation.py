from chatbot.robot import constants, utils
from chatbot.schemas.core import TxtEntity, Paginate, Page


class OfficeService:

    def __init__(self, file: str):
        self.office_list = TxtEntity(cols=['name','color'],
                                     file=constants.getConfigData(file))

    def office_all(self) -> list:
        return self.office_list.serialize()


class DoctorService:
    def __init__(self, file: str):
        self.doctor_list = TxtEntity(split=' ',cols=['py','name'],
                                     file=constants.getConfigData(file))

    def doctor_all(self) -> dict:
        doctor_dict = dict()
        for doctor in self.doctor_list.serialize():
            names = doctor_dict.get(doctor["py"], [])
            names.append(doctor["name"])
            doctor_dict.update({doctor["py"]: names})
        return doctor_dict

    def query_doctor_page(self, paginate: Paginate, kw: str = None) -> Page:
        data = []
        # 过滤
        for doctor in self.doctor_list.serialize():
            if not doctor["py"].startswith(kw):
                continue
            data.append(doctor["name"])
        paginate.set_total(len(data))
        return Page(paginate=paginate, content=utils.page_list(
            data=data, page=paginate.page, paginate_by=paginate.paginate_by))

class FaqService:

    def __init__(self, file: str):
        self.faq_list =  TxtEntity(file=constants.getConfigData(file))

    def faq_all(self) -> list:
        return self.faq_list.serialize()

    def query_faq_page(self, paginate: Paginate, kw: str = None) -> Page:
        content = self.faq_all()
        paginate.set_total(len(content))
        return Page(paginate=paginate, content=utils.page_list(
            data=content, page=paginate.page, paginate_by=paginate.paginate_by))
