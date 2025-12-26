
import inspect
try:
    print(inspect.getsource(FileIdMessage.time_created.fset))
except Exception as e:
    print(e)
