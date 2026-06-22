# Code management

## Management
- 코드를 수정한 후, 사용되지 않는 함수/클래스 등은 주석으로 식별자를 추가한다.
example
```python
def unused_function():
    """
    This function is not used anymore.
    """
    # some codeblocks here
    ...
    ...
```

## Test
- 코드 작성 후, 테스트를 수행하기 전 다음 command를 이용하여 linting을 수행한다.
```bash
ruff check .
ruff format .
```
- 이를 통과한 경우, 'test' 디렉토리에 포함된 test용 코드를 실행하여 구현한 기능이 잘 수행되는지 판단한다.


## 실패 시 처리
- 테스트 실패 시 테스트 실행 파일을 삭제하지 않는다.
- output으로 발생한 메시지를 통해 어떤 문제가 있는지 먼저 파악한다.
- 파악한 것을 바탕으로 문제점을 수정한 후, test를 다시 수행한다.