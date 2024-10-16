from repository.params import DATA_BASE_CONFIG
from sqlalchemy import create_engine
from playwright.sync_api import sync_playwright
from repository.database import Database
import time, os, boto3

bizops_host       = DATA_BASE_CONFIG["bizops"]["bizops_host"]
bizops_user       = DATA_BASE_CONFIG["bizops"]["bizops_user"]
bizops_password   = DATA_BASE_CONFIG["bizops"]["bizops_password"]
ops_mail          = DATA_BASE_CONFIG["bizops"]["ops_mail"]
ops_mail_password = DATA_BASE_CONFIG["bizops"]["ops_mail_password"]
s3_access_key_id  = DATA_BASE_CONFIG["bizops"]["s3_access_key_id"] 
s3_secret_access_key = DATA_BASE_CONFIG["bizops"]["s3_secret_access_key"]     

class PageController():
    def __init__(self):
        self.bizops_host = bizops_host       
        self.bizops_user = bizops_user       
        self.bizops_password = bizops_password   
        self.ops_mail = ops_mail          
        self.ops_mail_password = ops_mail_password 
        self.engine = create_engine(f'postgresql+psycopg2://{self.bizops_user}:{self.bizops_password}@{self.bizops_host}/bizops')
        self.download_dir = os.path.join(os.getcwd(), 'src', 'downloads')
        self.bucket_name = 'bizops-ai'
        self.s3_access_key_id = s3_access_key_id
        self.s3_secret_access_key = s3_secret_access_key
        
    def __close_browser(self, browser, playwright):
        try:
            if browser: browser.close()
            if playwright: playwright.stop()
        except Exception as e: print(f".... Error during cleanup: {e}")

    def __start_driver(self):
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        return page, browser, playwright

    def __login_google_sites(self):
        url = 'https://sites.google.com/u/0/new?pli=1&authuser=0&tgif=d'
        page, browser, playwright = self.__start_driver()
        try:
            page.goto(url, timeout=60000)
            time.sleep(3)

            print('.... Entering username')
            page.wait_for_selector('//*[@id="identifierId"]', timeout=30000).fill(self.ops_mail)

            print('.... Click Next after entering username')
            page.click('//*[@id="identifierNext"]')
            time.sleep(3)

            print('.... Entering password')
            page.wait_for_selector('//*[@id="password"]', timeout=10000).fill(self.ops_mail_password)

            print('.... Click Next after entering password')
            page.click('//*[@id="passwordNext"]')
            time.sleep(5)
            # page.screenshot(path=os.path.join(self.download_dir, 'screenshot.png'), full_page=True)                    
            return page, browser, playwright
        except Exception as e:
            print(f"An error occurred: {e}")
        
    def __download_page_as_pdf(self, description, page, url, has_dropdown):
        try: 
            print(f'...... Processing page to pdf and downloading: {description} ')
            page.goto(url, timeout=60000)
            time.sleep(3)
            
            if has_dropdown:
                query = f"SELECT description, url, button_path FROM ai.page_dropdown_mapping WHERE url = '{url}' order by id asc;"
                db = Database('bizops')
                dropdowns = db.query_result_list(query)

                for dropdown in dropdowns:
                    button_path = dropdown[2]
                    try:
                        page.wait_for_selector(button_path, timeout=10000)
                        page.click(button_path)
                        page.wait_for_timeout(3000)                        
                        time.sleep(2)                
                    except Exception as e:
                        print(f"Error clicking dropdown: {e}")         
                
            page.pdf(path=os.path.join(self.download_dir, f'{description}.pdf'))
            time.sleep(3)
        except Exception as e:
            print(f"An error occurred: {e}")            
    
    def __process_pages(self, theme_id:int = None):
        query = 'select id, theme_id, description, url, updated_date, has_dropdown from ai.page_control'
        if theme_id: query = f'{query} where theme_id = {theme_id}'

        db = Database('bizops')
        pages_list = db.query_result_list(query)              

        if len(pages_list) > 0:
            page, browser, playwright = self.__login_google_sites()
            if page:
                for single_page in pages_list:
                    theme_id     = single_page[1]
                    description  = single_page[2]
                    url          = single_page[3]
                    has_dropdown = single_page[5]

                    self.__download_page_as_pdf(description, page, url, has_dropdown)

            if browser and playwright:
                self.__close_browser(browser, playwright)
                print(".... Browser and Playwright closed successfully.")

    def __list_files_and_send_to_s3(self):
        folder_path = self.download_dir
        try:
            files = os.listdir(folder_path)

            for file in files:
                file_name, file_extension = os.path.splitext(file)
                file_name = file_name.lower()
                doc_name = self.__get_file_name(file_name)

                print(f'........ Sending {doc_name} to s3 folder: {file_name}')
                self.__send_files_to_s3(file_name, file_extension, doc_name)
                
        except FileNotFoundError:
            print(f"Folder '{folder_path}' not found.")

    def __clean_local_files(self):
        folder_path = self.download_dir
        try:
            files = os.listdir(folder_path)
            print(f'.... Listing files: \n   {files}')

            for file in files:
                file_path = os.path.join(folder_path, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"Deleted: {file_path}")

        except FileNotFoundError:
            print(f"Folder '{folder_path}' not found.")

    def __format_file_name(self, file_name: str) -> str:
        try:
            formatted_name = file_name.replace(' ', '_').lower()
            return formatted_name
        except AttributeError as e:
            print(f"Error formatting file name: {e} - Ensure that file_name is a string.")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

    def __get_file_name(self, description):
        query = f"SELECT DISTINCT pt.description FROM ai.page_theme pt JOIN ai.page_control pc ON pt.id = pc.theme_id WHERE LOWER(pc.description) = LOWER('{description}');"
        db = Database('bizops')
        try:
            result = db.query_result_list(query)
            if result:
                file_name = result[0][0]
                formatted_file_name = self.__format_file_name(file_name)
                return formatted_file_name
            else:
                raise ValueError(f"No result found for description: {description}")
        except (IndexError, ValueError) as e:
            print(f"Error retrieving file name: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

    def __send_files_to_s3(self, file_name, file_extension, doc_name, retries=5, wait_time=10):
        folder_path = self.download_dir
        file_name = file_name + file_extension
        s3_file_path = os.path.join(folder_path, file_name)
        attempt = 0

        while not os.path.exists(s3_file_path) and attempt < retries:
            print(f"........ Waiting for file {file_name} to appear .... attempt {attempt + 1}/{retries}")
            time.sleep(wait_time)
            attempt += 1

        if not os.path.exists(s3_file_path):
            raise FileNotFoundError(f"........ File '{file_name}' not found in folder '{folder_path}'")
        
        try:
            s3_client = boto3.client('s3', aws_access_key_id=self.s3_access_key_id, aws_secret_access_key=self.s3_secret_access_key)
            with open(s3_file_path, 'rb') as f:
                s3_key = f'{doc_name}/{file_name}'
                s3_client.upload_fileobj(f, self.bucket_name, s3_key)
            print(f"........ File '{s3_file_path}' uploaded to S3 bucket under '{s3_key}'")

        except Exception as e:
            raise Exception(f"Error sending file to AWS S3: {e}")

    def scrap_datasource(self, theme_id:int = None):
        self.__process_pages(theme_id)            

    def process_file_to_storage(self):        
        self.__list_files_and_send_to_s3()
        
    def clean_local_files(self):
        self.__clean_local_files()


