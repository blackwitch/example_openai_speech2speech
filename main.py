import asyncio
import websockets
import pyaudio
import base64
import json
import os
import time
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")

# 오디오 설정
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 24000  # 입력 및 출력 샘플링 속도 (OpenAI Realtime API는 24kHz를 권장할 수 있음)

# PyAudio 객체 초기화
audio = pyaudio.PyAudio()

# 입력 스트림 (마이크)
input_stream = None
# 출력 스트림 (스피커)
output_stream = None

async def run_s2s():
    global input_stream, output_stream, audio

    if not API_KEY:
        print("오류: OPENAI_API_KEY가 .env 파일에 설정되지 않았습니다.")
        return

    uri = "wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview" # OpenAI Realtime API WebSocket URI
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "OpenAI-Beta": "realtime=v1" # OpenAI Realtime API 베타 헤더
    }
    
    print(f"OpenAI API Key (일부): {API_KEY[:5]}...{API_KEY[-5:]}")
    print(f"{RATE} Hz, {CHANNELS} 채널, PCM16 오디오로 OpenAI Realtime API에 연결합니다.")

    try:
        # 오디오 스트림 초기화
        input_stream = audio.open(format=FORMAT,
                                  channels=CHANNELS,
                                  rate=RATE,
                                  input=True,
                                  frames_per_buffer=CHUNK)
        print("마이크 입력 스트림이 열렸습니다.")

        output_stream = audio.open(format=FORMAT,
                                   channels=CHANNELS,
                                   rate=RATE,
                                   output=True,
                                   frames_per_buffer=CHUNK)
        print("스피커 출력 스트림이 열렸습니다.")

        async with websockets.connect(
            uri, 
            additional_headers=headers, 
            ping_interval=None, # 클라이언트 자동 ping 비활성화
            ping_timeout=75 # openai는 45초
        ) as websocket:
            print("WebSocket 연결 성공.")
            
            session_init_message = {
                "event_id": "event_123",
                "type": "session.update",
                "session": {
                    "modalities": ["text","audio"],
                    "instructions": "You are a helpful assistant.",
                    "voice": "alloy", #  (예: nova, echo, fable, onyx, nova, shimmer)
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": "whisper-1"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                        "create_response": True
                    },
                    "tools": [
                        {
                            "type": "function",
                            "name": "get_weather",
                            "description": "Get the current weather...",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "location": { "type": "string" }
                                },
                                "required": ["location"]
                            }
                        }
                    ],
                    "tool_choice": "auto",
                    "temperature": 0.8,
                    "max_response_output_tokens": "inf"
                }
            }            
            print(f"세션 초기화 요청 전송 중: {json.dumps(session_init_message)}")
            await websocket.send(json.dumps(session_init_message))
            
            # 세션 초기화 응답 수신
            init_response_raw = await websocket.recv()
            init_response = json.loads(init_response_raw)
            print(f"세션 초기화 응답 수신: {init_response}")

            if init_response.get("type") == "session.created":
                print(f"세션 생성 성공. Session ID: {init_response.get('session').get('id')}")
            elif init_response.get("type") == "error":
                print(f"세션 생성 오류: {init_response.get('message')}")
                return
            else:
                print(f"예상치 못한 세션 초기화 응답: {init_response}")
                return

            print("마이크에서 오디오를 듣고 OpenAI로 스트리밍합니다. Ctrl+C로 종료합니다.")

            async def send_audio():
                try:
                    # 예시: 마지막 오디오 전송 후 일정 시간(예: 1초)이 지나면 commit
                    last_audio_time = time.time()
                    min_silence_duration_for_commit = 2.0 # 예시 값

                    while True:
                        data = input_stream.read(CHUNK, exception_on_overflow=False)
                        encoded_data = base64.b64encode(data).decode('utf-8')
                        audio_input_message = {
                            "type": "input_audio_buffer.append",
                            "audio": encoded_data
                        }
                        await websocket.send(json.dumps(audio_input_message))
                        last_audio_time = time.time() # 오디오 전송 시간 업데이트
                        await asyncio.sleep(0.01) 

                        # 임시: commit 로직 추가 (실제로는 VAD 등을 사용하는 것이 더 정교함)
                        # 이 부분은 API 문서와 실제 사용 사례에 맞게 조정 필요
                        # if time.time() - last_audio_time > min_silence_duration_for_commit:
                        #     print("일정 시간 동안 오디오 입력 없어 commit 메시지 전송 시도")
                        #     commit_message = { "type": "input_audio_buffer.commit" }
                        #     await websocket.send(json.dumps(commit_message))
                        #     # commit 후에는 다시 last_audio_time을 초기화하거나 다른 로직 필요할 수 있음

                except websockets.exceptions.ConnectionClosed:
                    print("오디오 전송 중 WebSocket 연결이 닫혔습니다.")
                except Exception as e:
                    print(f"오디오 전송 중 오류 발생: {e}")
                finally:
                    print("오디오 전송 루프 종료.")

            async def receive_responses():
                try:
                    async for message_raw in websocket:
                        try:
                            response_data = json.loads(message_raw)
                            # print(f"수신 메시지 (RAW): {message_raw}") # 원시 메시지 로깅 추가
                            # print(f"수신 메시지 (PARSED): {json.dumps(response_data, indent=2)}") # 파싱된 메시지 상세 로깅 추가
                        except json.JSONDecodeError as e:
                            print(f"수신 메시지 JSON 디코딩 오류: {e} - 원시 메시지: {message_raw}")
                            continue # 다음 메시지 처리를 위해 계속 진행

                        if response_data.get("type") == "response.done":
                            resp = response_data.get("response")
                            if resp:
                                print(f"resp 응답 완료! {json.dumps(resp, indent=2)}")
                            else:
                                print("응답 완료 메시지에 응답 데이터 없음.")
                            
                            resp2 = resp.get("output")
                            if resp2 and len(resp2) > 0:
                                content = resp2[0].get("content")
                                if content:
                                    transcript = resp2[0].get("content")[0].get("transcript")
                                    if transcript:
                                        print(f"수신 완료 텍스트  : {transcript}")
                                else:
                                    print("응답 데이터에 텍스트 없음.")
                        elif response_data.get("type") == "response.audio.delta":
                            audio_b64 = response_data.get("delta")
                            print("response.audio.delta 수신됨.")
                            if audio_b64:
                                audio_content = base64.b64decode(audio_b64)
                                output_stream.write(audio_content)
                            else:
                                print("응답 데이터에 오디오 없음.")

                        elif response_data.get("type") == "response.transcript":
                            transcript = response_data.get("transcript")
                            is_final = response_data.get("is_final", False)
                            print(f"Transcript (final: {is_final}): {transcript}")

                        elif response_data.get("type") == "response.text":
                            text = response_data.get("text")
                            print(f"Text Response: {text}")

                        elif response_data.get("type") == "session.terminated":
                            print(f"세션 종료됨: {response_data.get('reason', '이유 명시 안됨')}")
                            print(f"전체 세션 종료 응답: {json.dumps(response_data, indent=2)}")
                            break 

                        elif response_data.get("type") == "error":
                            error_message = response_data.get('message')
                            print(f"API 오류 수신: {error_message}")
                            print(f"전체 오류 응답: {json.dumps(response_data, indent=2)}") # 전체 오류 메시지 출력
                            # 오류 발생 시 더 이상 진행 의미 없을 수 있으므로 break 고려
                            break # 오류 발생 시 수신 루프 종료
                except websockets.exceptions.ConnectionClosedOK:
                    print("WebSocket 연결이 정상적으로 닫혔습니다 (수신 측).")
                except websockets.exceptions.ConnectionClosedError as e:
                    print(f"WebSocket 연결 오류로 닫혔습니다 (수신 측): {e}")
                except json.JSONDecodeError as e:
                    print(f"수신 메시지 JSON 디코딩 오류: {e} - 메시지: {message_raw}")
                except Exception as e:
                    print(f"응답 처리 중 오류 발생: {e}")
                finally:
                    print("응답 수신 루프 종료.")

            send_task = asyncio.create_task(send_audio())
            receive_task = asyncio.create_task(receive_responses())

            done, pending = await asyncio.wait(
                [send_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()

    except websockets.exceptions.InvalidURI:
        print(f"오류: 잘못된 WebSocket URI입니다: {uri}")
    except websockets.exceptions.InvalidHandshake as e:
        print(f"WebSocket 핸드셰이크 실패: {e}. API 키 또는 'OpenAI-Beta' 헤더를 확인하세요.")
    except ConnectionRefusedError:
        print("오류: WebSocket 연결이 거부되었습니다. 서버 상태 및 네트워크를 확인하세요.")
    except Exception as e:
        print(f"처리 중 예외 발생: {e}")
    finally:
        print("스트림 및 오디오 리소스 정리 중...")
        if input_stream:
            if input_stream.is_active():
                input_stream.stop_stream()
            input_stream.close()
            print("마이크 입력 스트림이 닫혔습니다.")
        
        if output_stream:
            if output_stream.is_active():
                output_stream.stop_stream()
            output_stream.close()
            print("스피커 출력 스트림이 닫혔습니다.")
            
        if audio:
            audio.terminate()
            print("PyAudio가 종료되었습니다.")
        print("정리 완료.")

if __name__ == "__main__":
    try:
        asyncio.run(run_s2s())
    except KeyboardInterrupt:
        print("\n🛑 프로그램이 사용자에 의해 중단되었습니다.")
    finally:
        print("프로그램 종료.")
