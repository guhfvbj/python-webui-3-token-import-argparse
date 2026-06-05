import unittest

from core import extract_videos_from_course_data


class ExtractVideosFromCourseDataTest(unittest.TestCase):
    def test_extracts_videos_from_chapter_tree(self):
        course_data = {
            "chapterResTree": [
                {
                    "id": "chapter-1",
                    "name": "章节一",
                    "resList": [
                        {"id": 101, "type": "1", "resTitle": "视频一", "progress": 0},
                        {"id": 102, "type": "2", "resTitle": "文档一"},
                    ],
                }
            ]
        }

        videos = extract_videos_from_course_data(course_data)

        self.assertEqual([video["res_id"] for video in videos], [101])
        self.assertEqual(videos[0]["chapter"], "章节一")
        self.assertEqual(videos[0]["title"], "视频一")

    def test_extracts_direct_videos_without_chapters(self):
        course_data = {
            "courseName": "无分章课程",
            "chapterResTree": [
                {"id": 201, "name": "独立视频一"},
                {"id": 202, "name": "独立视频二"},
            ],
        }

        videos = extract_videos_from_course_data(course_data)

        self.assertEqual([video["res_id"] for video in videos], [201, 202])
        self.assertEqual([video["title"] for video in videos], ["独立视频一", "独立视频二"])
        self.assertEqual([video["chapter"] for video in videos], ["", ""])

    def test_extracts_direct_videos_from_resource_like_lists(self):
        course_data = {
            "courseResourceList": [
                {"resId": "r-301", "title": "资源视频一"},
                {"videoId": "v-302", "videoName": "资源视频二", "durationSecond": 120},
            ]
        }

        videos = extract_videos_from_course_data(course_data)

        self.assertEqual([video["res_id"] for video in videos], ["r-301", "v-302"])
        self.assertEqual([video["title"] for video in videos], ["资源视频一", "资源视频二"])


if __name__ == "__main__":
    unittest.main()
