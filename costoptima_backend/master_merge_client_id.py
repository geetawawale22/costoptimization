import pandas as pd
import openpyxl
import win32com.client
import shutil
import os
import sys
module_path = os.path.abspath(r"E:\Sharepoint_Reusable\\")
sys.path.append(module_path)
from sharepoint_download_clientid_reusable import download_file
from sharepoint_upload_clientid_reusable import sharepoint_upload_file

basepath = r'D:\PCM_Sharepoint\PCM_Master'
auth_matrix= r'D:\PCM_Sharepoint\Authority_Matrix.xlsx'
outputpath = basepath
masterpath = r'D:\RPA_BOT2\PCM_Phase3'
duplicate_path = r'D:\PCM\Duplicate'


site_hostname = 'mahindraonline.sharepoint.com'
site_path = '/sites/IPRAStaging/'
folder_path = "RPA_224/RPA/PCM_Master/"


download_file(site_hostname , site_path , "Authority_Matrix.xlsx" , folder_path , r"D:\PCM_Sharepoint\Authority_Matrix.xlsx")


try:
    shutil.copy(r"D:\PCM_Sharepoint\Authority_Matrix.xlsx" , r"D:\PCM\Authority_Matrix.xlsx")
except:
    print("Error")
mas_spl = pd.read_excel(auth_matrix , header=[0] , sheet_name='Master_Split')
col_mer = pd.read_excel(auth_matrix , header=[0] , sheet_name='Column_Merging')
mas_head = pd.read_excel(auth_matrix , header=[0] , sheet_name='Master_headers' , converters={'Header':str})

			
head_dict = dict(zip(mas_head['Colname'] , mas_head['Header']))
unq_dict = dict(zip(mas_head['Colname'] , mas_head['Unique']))
sheet_dict = dict(zip(mas_head['Colname'] , mas_head['SheetName']))
pos_dict = dict(zip(mas_head['Colname'] , mas_head['Position']))

def ensure_directory_exists(file_path):
    # Split the file path into directories
    directories = file_path.split(os.path.sep)
    # Initialize current path
    current_path = directories[0]  # Start with the root directory
    # Iterate through each directory and ensure it exists
    for directory in directories[1:]:
        current_path = os.path.join(current_path, directory)
        if not os.path.exists(current_path):
            os.makedirs(current_path)  # Create the directory if it doesn't exist
 


def break_link(master):
    xl = win32com.client.Dispatch('Excel.Application')
    #win32.Dispatch('Excel.Application')
    xl.DisplayAlerts=False
    wb = xl.Workbooks.Open(master)
    xl.DisplayAlerts=False
    link=wb.LinkSources(Type=1)
    print(link)
    try:
        for i in range(len(link)):
            xl.DisplayAlerts=False
            wb.BreakLink(Name=link[i], Type=1)       #### To break any external links with the input sheet
    except:
        print("No external links")
    wb.Save()
    wb.Close()
    xl.Quit()

def read_without_formula(master ,masterwithoutformula ):
    try:
        break_link(master)
        bookmaster = openpyxl.load_workbook(master,data_only=True)
        bookmaster.save(masterwithoutformula)
    
    except:
        print("skip")

def type0(writer , coln ,listfs ,unq_val,pos_val,sheet_val):
    print(listfs)
    for newkk in range(len(listfs)):
        listfsval = listfs[newkk]
        path_chk = listfs[newkk].replace(basepath , "")
        folder_chk = path_chk
        path_chk = basepath+"\\"+path_chk
        filename = listfsval+"\\"+coln+".xlsx"
        print("filename is " , filename)
        print(path_chk+"\\"+coln+".xlsx")

        # Example usage:
        print("path_chk " , path_chk)
        ensure_directory_exists(path_chk.replace("\\PCM_Sharepoint" , ""))
        try:
            print(folder_chk , " folder_chk")
            print(path_chk+"\\"+coln+".xlsx" , " second")
            print(coln+".xlsx" , " third")
            download_file(site_hostname , site_path , coln+".xlsx" , folder_path+folder_chk , path_chk+"\\"+coln+".xlsx")
            #download_cloud(folder_chk ,  path_chk+"\\"+coln+".xlsx", coln+".xlsx")
            print("Sucessfully Downloaded file to Sharepoint")
        except:
            print('Error while downloading East zones files')
##        shutil.copy(filename , path_chk+"\\"+coln+".xlsx")
        read_without_formula(path_chk+"\\"+coln+".xlsx" ,path_chk+"\\"+coln+".xlsx" )
        
        df1 = pd.read_excel(path_chk+"\\"+coln+".xlsx" , header=[0])
        if(newkk == 0):
            df = df1
        else:
            df = pd.concat([df, df1])

        df2 = df1[unq_val]
        # Selecting duplicate rows except first
        # occurrence based on req columns
        duplicate = df2[df2.duplicated()]
        duplicate.to_excel(path_chk+"\\"+coln+"_duplicate.xlsx" , index=False) 
    if(pd.isnull(pos_val)):    
        df.to_excel(writer, sheet_name=sheet_val, startrow=0,startcol=0, header=True, index=False,float_format='%.3f')
    else:
        df.to_excel(writer, sheet_name=sheet_val, startrow=0,startcol=int(pos_val), header=True, index=False,float_format='%.3f')

def type1(writer , coln ,listfs ,unq_val,pos_val,sheet_val):
    print(listfs)
    for newkk in range(len(listfs)):
        listfsval = listfs[newkk]
        path_chk = listfs[newkk].replace(basepath , "")
        folder_chk = path_chk
        path_chk = basepath+"\\"+path_chk
        filename = listfsval+"\\"+coln+".xlsx"
        print("filename is " , filename)
        print(path_chk+"\\"+coln+".xlsx")

        # Example usage:
        print("path_chk " , path_chk)
        ensure_directory_exists(path_chk.replace("\\PCM_Sharepoint" , ""))
        try:
            print(folder_chk , " folder_chk")
            print(path_chk+"\\"+coln+".xlsx" , " second")
            print(coln+".xlsx" , " third")
            download_file(site_hostname , site_path , coln+".xlsx" , folder_path+folder_chk , path_chk+"\\"+coln+".xlsx")
            #download_cloud(folder_chk ,  path_chk+"\\"+coln+".xlsx", coln+".xlsx")
            print("Sucessfully Downloaded file to Sharepoint")
        except:
            print('Error while downloading East zones files')
##        shutil.copy(filename , path_chk+"\\"+coln+".xlsx")
        read_without_formula(path_chk+"\\"+coln+".xlsx" ,path_chk+"\\"+coln+".xlsx" )
        
        df1 = pd.read_excel(path_chk+"\\"+coln+".xlsx" , header=[1])
        if(newkk == 0):
            df = df1
        else:
            df = pd.concat([df, df1])

        df2 = df1[unq_val]
        # Selecting duplicate rows except first
        # occurrence based on req columns
        duplicate = df2[df2.duplicated()]
        duplicate.to_excel(path_chk+"\\"+coln+"_duplicate.xlsx" , index=False) 
        
    if(pd.isnull(pos_val)):    
        df.to_excel(writer, sheet_name=sheet_val, startrow=1,startcol=0, header=True, index=False,float_format='%.3f')
    else:
        df.to_excel(writer, sheet_name=sheet_val, startrow=1,startcol=int(pos_val), header=True, index=False,float_format='%.3f')


def typesingle(writer , coln ,listfs ,unq_val,pos_val,sheet_val):
    
    listfsval = listfs[0]
    path_chk = listfs[0].replace(basepath , "")
    folder_chk = path_chk
    path_chk = basepath+"\\"+path_chk
    filename = listfsval+"\\"+coln+".xlsx"
    print("filename is " , filename)
    print(path_chk+"\\"+coln+".xlsx")

    # Example usage:
    print("path_chk " , path_chk)
    ensure_directory_exists(path_chk.replace("\\PCM_Sharepoint" , ""))
    try:
        print(folder_chk , " folder_chk")
        print(path_chk+"\\"+coln+".xlsx" , " second")
        print(coln+".xlsx" , " third")
        download_file(site_hostname , site_path , coln+".xlsx" , folder_path+folder_chk , path_chk+"\\"+coln+".xlsx")
        #download_cloud(folder_chk ,  path_chk+"\\"+coln+".xlsx", coln+".xlsx")
        print("Sucessfully Downloaded file to Sharepoint")
    except:
        print('Error while downloading East zones files')
##    shutil.copy(filename , path_chk+"\\"+coln+".xlsx")
    read_without_formula(path_chk+"\\"+coln+".xlsx" ,path_chk+"\\"+coln+".xlsx" )
    
    df1 = pd.read_excel(path_chk+"\\"+coln+".xlsx" , header=None)
    
    
    df1.to_excel(writer, sheet_name=sheet_val, startrow=0,startcol=0, header=None, index=False,float_format='%.3f')


def typemerge(writer ,coln ,listfs ,unq_val,pos_val,sheet_val):
    print(listfs)
    for newkk in range(len(listfs)):
        listfsval = listfs[newkk]
        path_chk = listfs[newkk].replace(basepath , "")
        folder_chk = path_chk
        path_chk = basepath+"\\"+path_chk
        filename = listfsval+"\\"+coln+".xlsx"
        print("filename is " , filename)
        print(path_chk+"\\"+coln+".xlsx")

        # Example usage:
        print("path_chk " , path_chk)
        ensure_directory_exists(path_chk.replace("\\PCM_Sharepoint" , ""))
        try:
            print(folder_chk , " folder_chk")
            print(path_chk+"\\"+coln+".xlsx" , " second")
            print(coln+".xlsx" , " third")
            download_file(site_hostname , site_path , coln+".xlsx" , folder_path+folder_chk , path_chk+"\\"+coln+".xlsx")
            #download_cloud(folder_chk ,  path_chk+"\\"+coln+".xlsx", coln+".xlsx")
            print("Sucessfully Downloaded file to Sharepoint")
        except:
            print('Error while downloading East zones files')

        
##        shutil.copy(filename , path_chk+"\\"+coln+".xlsx")
        read_without_formula(path_chk+"\\"+coln+".xlsx" ,path_chk+"\\"+coln+".xlsx" )
        
        #df1 = pd.read_excel(path_chk+"\\"+coln+".xlsx" , header=[0])
        df = pd.read_excel(path_chk+"\\"+coln+".xlsx", header=[0])
        df1 = pd.read_excel(path_chk+"\\"+coln+".xlsx", header=[1])
        # concatenate first two rows with header
        mat_mast =[]
        for mmknd in range(len(col_mer[coln])):
            mmkndval = col_mer[coln][mmknd]
            print(mmkndval)
            try:
                print(len(mmkndval))
                mat_mast.append(mmkndval)
            except:
                print("nan no need to append")

                
        new_header = []
        for i in range(len(df1.columns)):
            val1 = df1.columns[i]
            val2 = df.columns[i]

            for kk in range(len(mat_mast)):
                if(mat_mast[kk] in val1):
                    val1 = mat_mast[kk]
                    break

            if(val1 in mat_mast):
                print(val2)
                val2 = val2.split(".")[0]
                val = val1+"_"+val2
            else:
                val = val1
            new_header.append(val)

            
        df1.columns.values[:] =new_header

        # set new header as index
        df1.to_excel(path_chk+"\\"+coln+"_test.xlsx" , index=False)
        df1.to_excel(path_chk+"\\"+coln+"_test_duplicate.xlsx" , index=False)

        df1 = pd.read_excel(path_chk+"\\"+coln+"_test.xlsx" , header=[0])
        #df1.drop_duplicates(subset=unq_val,keep='first', inplace=True)

        df2 = pd.read_excel(path_chk+"\\"+coln+"_test_duplicate.xlsx" , header=[0])
        df2 = df2[unq_val]
        # Selecting duplicate rows except first
        # occurrence based on req columns
        duplicate = df2[df2.duplicated()]
        duplicate.to_excel(path_chk+"\\"+coln+"_duplicate.xlsx" , index=False)
        if(newkk == 0):
            df3 = df1
        else:
            df3 = pd.concat([df3, df1])

        
    df3.to_excel("Final_master.xlsx" , index=False)
    df3 = pd.read_excel("Final_master.xlsx" , header=[0])
    df3.to_excel(writer, sheet_name=sheet_val, startrow=1,startcol=0, header=True, index=False,float_format='%.3f')
    collistmerge = list(df3.columns)
    worksheet_merge = writer.sheets[sheet_val]
    for lastit in range(len(collistmerge)):
        valmm = collistmerge[lastit]
        if("_" in valmm):
            header0=valmm.split("_")[1]
            header1=valmm.split("_")[0]
            worksheet_merge.write(0, lastit, header0)
            worksheet_merge.write(1, lastit, header1)
 
#shutil.copytree(r"\\10.2.152.195\prdshare\RPA\PCM_Master" , r"D:\PCM\Master")
writer = pd.ExcelWriter(outputpath+"\\"+'Master Rate Data.xlsx', engine='xlsxwriter')
for kik in range(len(mas_head)):
    coln = mas_head['Colname'][kik]
    head_val = head_dict[coln]
    unq_val = unq_dict[coln]
    sheet_val = sheet_dict[coln]
    pos_val = pos_dict[coln]

    split_mat = mas_spl[mas_spl[coln].notnull()]
    listfs = list(split_mat['Folder Structure'])
    if(head_val == '0'):
        df = type0(writer ,coln ,listfs ,unq_val,pos_val,sheet_val)
    elif(head_val == '1'):
        df = type1(writer ,coln ,listfs ,unq_val,pos_val,sheet_val)
    elif(head_val == 'Single'):
        df = typesingle(writer ,coln ,listfs ,unq_val,pos_val,sheet_val)
    elif(head_val == '0,1'):
        df = typemerge(writer ,coln ,listfs ,unq_val,pos_val,sheet_val)
        


writer.close()
try:
    sharepoint_upload_file(site_hostname , site_path , r"D:\PCM_Sharepoint\PCM_Master\Master Rate Data.xlsx" , folder_path )
    #upload_cloud("D:\PCM_Sharepoint\PCM_Master\Master Rate Data.xlsx" , "Master Rate Data.xlsx" )
    print("Sucessfully Uploaded file to Sharepoint")
except:
    print('Error while uploading Master Rate Data files')

    
shutil.copy(outputpath+"\\"+'Master Rate Data.xlsx', masterpath+"\\"+'Master Rate Data.xlsx')
##shutil.copy(outputpath+"\\"+'Master Rate Data.xlsx', basepath+"\\"+'Master Rate Data.xlsx')
